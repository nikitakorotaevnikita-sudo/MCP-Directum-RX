from src.services.qa_search import POLL_INTERVAL_SECONDS, QASearchService

COMPLETED_INFO = {
    "Id": "t1",
    "Status": "Completed",
    "SearchAreaName": "Документы ОГВ",
    "ErrorMessage": None,
    "Result": {
        "Answer": "Срок рассмотрения обращения — 30 дней.",
        "Score": 0.87,
        "Entities": [
            {
                "EntityId": "16660",
                "EntityName": "Ответное письмо №04-ОГ",
                "EntityLink": None,
                "EntityExtension": "docx",
                "Chunks": [
                    {"Text": "низкий фрагмент", "Score": 0.1},
                    {"Text": "Обращение рассматривается в течение 30 дней." + "x" * 600, "Score": 0.9},
                    {"Text": "средний фрагмент", "Score": 0.5},
                ],
            },
            {
                "EntityId": "abc",
                "EntityName": "Регламент",
                "EntityLink": "https://rx.example/Client/#/card/x/7",
                "EntityExtension": "pdf",
                "Chunks": [],
            },
        ],
    },
}


class FakeClient:
    def __init__(self, areas=None, start=None, infos=None):
        self.areas = [{"Id": 1, "Name": "Документы ОГВ"}, {"Id": 2, "Name": "Справка"}] if areas is None else areas
        self.start = {"Task": {"Id": "t1", "Status": "InProgress"}, "ErrorMessage": None} if start is None else start
        self.infos = list(infos or [COMPLETED_INFO])
        self.functions = []
        self.posts = []

    def call_function(self, path, params=None):
        self.functions.append(path)
        return self.areas

    def post(self, path, payload):
        self.posts.append((path, payload))
        if path.endswith("CreateSearchTask"):
            return self.start
        return self.infos.pop(0) if len(self.infos) > 1 else self.infos[0]

    def build_document_card_url(self, document_id):
        return f"https://rx.example/doc/{document_id}"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def service(client, clock=None):
    clock = clock or FakeClock()
    return QASearchService(client, sleep=clock.sleep, clock=clock)


def test_areas_listed():
    areas = service(FakeClient()).areas()

    assert [(area.id, area.name) for area in areas] == [(1, "Документы ОГВ"), (2, "Справка")]


def test_ask_uses_all_ready_areas_and_returns_answer_with_sources():
    client = FakeClient()

    answer = service(client).ask("Какой срок рассмотрения обращения?")

    assert client.functions == ["QASearchCore/GetReadySearchAreas"]
    assert client.posts[0] == ("QASearchCore/CreateSearchTask", {"question": "Какой срок рассмотрения обращения?", "searchAreaIds": [1, 2]})
    assert client.posts[1] == ("QASearchCore/GetSearchTaskInfo", {"taskId": "t1"})
    assert answer.status == "completed"
    assert answer.task_id == "t1"
    assert answer.answer == "Срок рассмотрения обращения — 30 дней."
    assert answer.score == 0.87
    assert answer.search_area == "Документы ОГВ"
    first, second = answer.sources
    assert first.name == "Ответное письмо №04-ОГ"
    assert first.url == "https://rx.example/doc/16660"
    assert first.extension == "docx"
    assert len(first.fragments) == 2
    assert first.fragments[0].startswith("Обращение рассматривается")
    assert len(first.fragments[0]) == 500
    assert first.fragments[1] == "средний фрагмент"
    assert second.url == "https://rx.example/Client/#/card/x/7"


def test_explicit_areas_skip_listing():
    client = FakeClient()

    service(client).ask("вопрос", area_ids=[2])

    assert client.functions == []
    assert client.posts[0][1]["searchAreaIds"] == [2]


def test_polls_until_completed():
    in_progress = {"Id": "t1", "Status": "InProgress", "Result": None, "ErrorMessage": None}
    client = FakeClient(infos=[in_progress, in_progress, COMPLETED_INFO])
    clock = FakeClock()

    answer = service(client, clock).ask("вопрос", wait_seconds=30)

    assert answer.status == "completed"
    assert clock.sleeps == [POLL_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS]


def test_timeout_returns_in_progress_with_task_id():
    in_progress = {"Id": "t1", "Status": "InProgress", "Result": None, "ErrorMessage": None}
    client = FakeClient(infos=[in_progress])
    clock = FakeClock()

    answer = service(client, clock).ask("вопрос", wait_seconds=5)

    assert answer.status == "in_progress"
    assert answer.task_id == "t1"
    assert "get_ask_documents_result" in answer.message
    assert clock.now >= 5


def test_result_checks_once_without_waiting():
    in_progress = {"Id": "t9", "Status": "InProgress", "Result": None, "ErrorMessage": None}
    client = FakeClient(infos=[in_progress])
    clock = FakeClock()

    answer = service(client, clock).result("t9")

    assert answer.status == "in_progress"
    assert client.posts == [("QASearchCore/GetSearchTaskInfo", {"taskId": "t9"})]
    assert clock.sleeps == []


def test_no_areas_means_unavailable():
    client = FakeClient(areas=[])

    answer = service(client).ask("вопрос")

    assert answer.status == "unavailable"
    assert "не настроен" in answer.message
    assert client.posts == []


def test_start_error_reported():
    client = FakeClient(start={"Task": None, "ErrorMessage": "QASearchCore. CreateSearchTasks. Failed to connect to RAG service"})

    answer = service(client).ask("вопрос")

    assert answer.status == "error"
    assert "Failed to connect to RAG service" in answer.message


def test_task_error_reported():
    client = FakeClient(infos=[{"Id": "t1", "Status": "Error", "Result": None, "ErrorMessage": "index missing"}])

    answer = service(client).ask("вопрос")

    assert answer.status == "error"
    assert "index missing" in answer.message


def test_value_wrapped_responses_are_unwrapped():
    client = FakeClient(
        start={"value": {"Task": {"Id": "t1"}, "ErrorMessage": None}},
        infos=[{"@odata.context": "x", **COMPLETED_INFO}],
    )

    assert service(client).ask("вопрос").status == "completed"


def test_in_progress_placeholder_result_is_not_completion():
    # Стенд присылает в InProgress пустую заготовку Result — это ещё не ответ.
    placeholder = {"Id": "t1", "Status": "InProgress", "ErrorMessage": "", "Result": {"Answer": None, "Score": 0.0, "Entities": []}}
    client = FakeClient(infos=[placeholder, COMPLETED_INFO])
    clock = FakeClock()

    answer = service(client, clock).ask("вопрос", wait_seconds=30)

    assert answer.status == "completed"
    assert answer.answer == "Срок рассмотрения обращения — 30 дней."
    assert clock.sleeps == [POLL_INTERVAL_SECONDS]


def test_empty_error_message_is_not_an_error():
    info = {**COMPLETED_INFO, "ErrorMessage": ""}

    assert service(FakeClient(infos=[info])).ask("вопрос").status == "completed"
