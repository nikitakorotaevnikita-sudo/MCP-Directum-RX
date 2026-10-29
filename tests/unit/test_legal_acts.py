from datetime import date, datetime, timedelta, timezone

import pytest

from src.services.directum_client import DirectumError
from src.services.legal_acts import KIND_FILTERS, LegalActsService

STAND_TZ = timezone(timedelta(hours=4))
NOW = datetime(2026, 6, 5, 12, 0, tzinfo=STAND_TZ)


def act_row(doc_id=15213, kind="Проект правового акта", **overrides):
    row = {
        "Id": doc_id,
        "Name": "Проект правового акта №26-044 от 29.05.2026 «О домовых чатах»",
        "Subject": "О домовых чатах",
        "RegistrationNumber": "26-044",
        "RegistrationDate": "2026-05-29T00:00:00+04:00",
        "IsNormative": True,
        "IsPublication": False,
        "DateOfPublication": None,
        "PlaceOfPublication": None,
        "RegNumberLegalAct": None,
        "LifeCycleState": "Active",
        "InternalApprovalState": "OnApproval",
        "HasVersions": True,
        "DocumentKind": {"Name": kind},
        "PreparedBy": {"Name": "Еремеева Елена Ивановна"},
        "Department": {"Name": "Правовое управление"},
        "OurSignatory": {"Name": "Губернатор"},
    }
    row.update(overrides)
    return row


def task_row(task_id=82294, attachments=(15213, 15215), **overrides):
    row = {
        "Id": task_id,
        "Subject": "Согласование: Проект правового акта №26-044",
        "Status": "InProcess",
        "Started": "2026-05-29T08:32:38+04:00",
        "Curator": {"Name": "Еремеева Елена Ивановна"},
        "InternalApprovers": [{"Approver": {"Name": "Андерсон Валерий Михайлович"}}],
        "OuterApprovers": [{"Counterparty": {"Name": "Прокуратура области"}}],
        "AttachmentDetails": [{"AttachmentId": item, "IsAutoPopulated": index > 0} for index, item in enumerate(attachments)],
    }
    row.update(overrides)
    return row


ASSIGNMENTS = [
    {"Id": 1, "Subject": "Согласуйте: Проект правового акта №26-044", "Created": "2026-05-29T09:11:04+04:00", "Deadline": "2026-06-01T09:00:00+04:00", "Performer": {"Name": "Шадаев Максут Игоревич"}, "Task": {"Id": 82294}},
    {"Id": 2, "Subject": "Примите решение о публикации: Проект", "Created": "2026-06-04T10:00:00+04:00", "Deadline": None, "Performer": {"Name": "Петров П.П."}, "Task": {"Id": 82294}},
]


class FakeClient:
    def __init__(self, docs=None, tasks=None, assignments=None, count=7, others=None):
        self.docs = {row["Id"]: row for row in (docs if docs is not None else [act_row()])}
        self.others = others or []
        self.tasks = tasks if tasks is not None else [task_row()]
        self.assignments = ASSIGNMENTS if assignments is None else assignments
        self.total = count
        self.calls = []
        self.counts = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        filter_ = kwargs["filter_"]
        if entity_set == "ILegalActsLegalDocumentBases":
            if filter_.startswith("ProjectId eq"):
                project_id = int(filter_.split("ProjectId eq ")[1].split(" ")[0])
                return [row for row in self.docs.values() if row.get("ProjectId") == project_id and "Проект" not in row["DocumentKind"]["Name"]]
            if "Id eq" in filter_ and "or" not in filter_ and "RegistrationNumber" not in filter_:
                doc_id = int(filter_.split("Id eq ")[1].split(" ")[0].rstrip(")"))
                return [self.docs[doc_id]] if doc_id in self.docs else []
            if "Id eq" in filter_:
                ids = {int(part.split("Id eq ")[1].rstrip(")").split(" ")[0]) for part in filter_.split(" or ")}
                return [row for doc_id, row in self.docs.items() if doc_id in ids]
            if "RegistrationNumber eq" in filter_:
                number = filter_.split("RegistrationNumber eq '")[1].split("'")[0]
                return [row for row in self.docs.values() if row["RegistrationNumber"] == number and "Проект" not in row["DocumentKind"]["Name"]]
            if "contains(RegNumberLegalAct" in filter_:
                return [row for row in self.docs.values() if row.get("RegNumberLegalAct")]
            return list(self.docs.values())
        if entity_set == "IOfficialDocuments":
            if filter_.startswith("LeadingDocument/Id eq"):
                leading = int(filter_.split("eq ")[1])
                return [row for row in self.others if row.get("Leading") == leading]
            return [row for row in self.others if not row.get("Leading")]
        if entity_set == "ILegalActsApprovalTasks":
            return self.tasks
        if entity_set == "IAssignments":
            return self.assignments
        raise AssertionError(entity_set)

    def count(self, entity_set, filter_=None):
        self.counts.append((entity_set, filter_))
        return self.total

    def build_document_card_url(self, document_id):
        return f"https://rx/doc/{document_id}"


def service(client):
    return LegalActsService(client, tz=STAND_TZ, now=lambda: NOW)


# --- find ---------------------------------------------------------------


def test_find_maps_summary_with_russian_labels():
    result = service(FakeClient()).find(text="домовых чатах")

    item = result.items[0]
    assert item.kind == "Проект правового акта"
    assert item.is_project is True
    assert item.approval_state == "OnApproval"
    assert item.approval_state_label == "На согласовании"
    assert item.lifecycle_state_label == "Действующий"
    assert item.registration_date == date(2026, 5, 29)
    assert item.url == "https://rx/doc/15213"
    assert result.total == 7


def test_find_builds_filter_from_all_criteria():
    client = FakeClient()

    service(client).find(
        text="чатах", kind="project", normative=True, published=False, approval_state="OnRework",
        date_from=date(2026, 1, 1), date_to=date(2026, 6, 30), limit=5,
    )

    entity_set, kwargs = client.calls[0]
    filter_ = kwargs["filter_"]
    assert entity_set == "ILegalActsLegalDocumentBases"
    assert "contains(Name,'чатах')" in filter_
    assert KIND_FILTERS["project"] in filter_
    assert "IsNormative eq true" in filter_ and "IsPublication eq false" in filter_
    assert "InternalApprovalState eq 'OnRework'" in filter_
    assert "RegistrationDate ge 2026-01-01T00:00:00+04:00" in filter_ and "RegistrationDate lt 2026-07-01T00:00:00+04:00" in filter_
    assert kwargs["top"] == 5
    assert client.counts[0][1] == filter_


def test_find_without_criteria_still_sends_filter():
    client = FakeClient()

    service(client).find()

    assert client.calls[0][1]["filter_"] == "Id gt 0"


def test_find_approval_state_none_means_not_sent():
    client = FakeClient()

    service(client).find(approval_state="none")

    assert "InternalApprovalState eq null" in client.calls[0][1]["filter_"]


@pytest.mark.parametrize("kwargs", [{"kind": "law"}, {"approval_state": "Maybe"}])
def test_find_rejects_unknown_values(kwargs):
    with pytest.raises(ValueError):
        service(FakeClient()).find(**kwargs)


# --- get ----------------------------------------------------------------


def test_get_project_with_approval_and_current_steps():
    detail = service(FakeClient()).get(15213)

    assert detail.act.id == 15213
    assert detail.prepared_by == "Еремеева Елена Ивановна"
    assert detail.department == "Правовое управление"
    assert detail.signatory == "Губернатор"
    assert detail.has_text is True
    approval = detail.approvals[0]
    assert approval.task_id == 82294
    assert approval.status_label == "в работе"
    assert approval.curator == "Еремеева Елена Ивановна"
    assert approval.internal_approvers == ["Андерсон Валерий Михайлович"]
    assert approval.outer_approvers == ["Прокуратура области"]
    assert approval.days_in_approval == 7
    first, second = approval.current_steps
    assert (first.performer, first.action, first.days_waiting, first.overdue) == ("Шадаев Максут Игоревич", "Согласуйте", 7, True)
    assert (second.action, second.days_waiting, second.overdue) == ("Примите решение о публикации", 1, False)


def test_get_uses_attachment_filter_for_tasks():
    client = FakeClient()

    service(client).get(15213)

    task_call = next(kw for es, kw in client.calls if es == "ILegalActsApprovalTasks")
    assert task_call["filter_"] == "AttachmentDetails/any(a: a/AttachmentId eq 15213)"


def test_get_project_links_adopted_act_by_number():
    project = act_row(RegNumberLegalAct="От 24.06.2026 № 015/26")
    adopted = act_row(doc_id=15751, kind="Правовой акт", RegistrationNumber="015/26", InternalApprovalState="Signed",
                      RegistrationDate="2026-06-24T00:00:00+04:00")

    detail = service(FakeClient(docs=[project, adopted], tasks=[])).get(15213)

    assert detail.adopted_act.id == 15751
    assert detail.adopted_act.approval_state_label == "Подписан"


def test_get_project_prefers_project_id_over_number():
    # Живой случай 16789: номер 001/26 повторяется по годам — акт «Тест» от 19.01.2026 не должен подставиться.
    project = act_row(doc_id=16789, RegNumberLegalAct="От 14.10.2026 № 001/26")
    old = act_row(doc_id=12172, kind="Правовой акт", RegistrationNumber="001/26", ProjectId=11954,
                  RegistrationDate="2026-01-19T00:00:00+04:00")
    adopted = act_row(doc_id=16808, kind="Правовой акт", RegistrationNumber="001/26", ProjectId=16789,
                      RegistrationDate="2026-10-14T00:00:00+04:00")

    assert service(FakeClient(docs=[project, old, adopted], tasks=[])).get(16789).adopted_act.id == 16808
    # Без ProjectId номер сверяется вместе с датой: чужой акт с тем же номером не берётся.
    assert service(FakeClient(docs=[project, old], tasks=[])).get(16789).adopted_act is None


def test_get_act_links_project_by_project_id():
    act = act_row(doc_id=16808, kind="Правовой акт", RegistrationNumber="001/26", ProjectId=16789)
    project = act_row(doc_id=16789, RegNumberLegalAct="От 14.10.2026 № 001/26")

    detail = service(FakeClient(docs=[act, project], tasks=[])).get(16808)

    assert [item.id for item in detail.projects] == [16789]


def test_get_relations_addenda_and_possibly_related():
    project = act_row(doc_id=16789, Subject="О региональном стандарте максимально допустимой доли расходов граждан",
                      HasRelations=True, Created="2026-10-14T13:48:22+04:00")
    others = [
        {"Id": 16900, "Name": "Приложение 1 к проекту", "Created": "2026-10-14T15:00:00+04:00", "Leading": 16789,
         "DocumentKind": {"Name": "Приложение к документу"}},
        {"Id": 16809, "Name": "Заключение ОРВ «ОРВ о стандарте доли расходов граждан»", "Created": "2026-10-14T14:20:06+04:00",
         "DocumentKind": {"Name": "Заключение ОРВ"}},
        {"Id": 16805, "Name": "Пояснительная записка к проекту о региональном стандарте максимально допустимой доли",
         "Created": "2026-10-14T13:52:13+04:00", "DocumentKind": {"Name": "Пояснительная записка"}},
        {"Id": 16817, "Name": "Пояснительная записка к новому постановлению", "Created": "2026-10-14T14:53:11+04:00",
         "DocumentKind": {"Name": "Пояснительная записка"}},
    ]
    client = FakeClient(docs=[project], tasks=[], others=others)

    detail = service(client).get(16789)

    assert detail.has_relations is True
    assert [(d.id, d.kind) for d in detail.addenda] == [(16900, "Приложение к документу")]
    assert [d.id for d in detail.possibly_related] == [16805, 16809]
    assert "не подтверждена" in detail.possibly_related[0].reason and detail.possibly_related[0].url == "https://rx/doc/16805"
    assert "не отдаёт" in detail.relations_note and "https://rx/doc/16789" in detail.relations_note
    related_call = next(kw for es, kw in client.calls if es == "IOfficialDocuments" and "HasRelations" in kw["filter_"])
    assert "Id ne 16789" in related_call["filter_"] and "contains(Name,'станда')" in related_call["filter_"]
    assert "Created ge 2026-08-15T13:48:22+04:00" in related_call["filter_"]


def test_get_without_relations_skips_guessing():
    client = FakeClient(docs=[act_row(HasRelations=False)], tasks=[])

    detail = service(client).get(15213)

    assert detail.possibly_related == [] and detail.relations_note == ""
    assert not any("HasRelations" in kw["filter_"] for es, kw in client.calls if es == "IOfficialDocuments")


def test_get_act_links_back_to_projects():
    act = act_row(doc_id=15751, kind="Правовой акт", RegistrationNumber="015/26", RegistrationDate="2026-06-24T00:00:00+04:00")
    project = act_row(doc_id=15749, RegNumberLegalAct="От 24.06.2026 № 015/26")
    other = act_row(doc_id=15700, RegNumberLegalAct="От 01.01.2026 № 1015/26")

    detail = service(FakeClient(docs=[act, project, other], tasks=[])).get(15751)

    assert [item.id for item in detail.projects] == [15749]


def test_get_missing_act_raises_not_found():
    with pytest.raises(DirectumError) as exc:
        service(FakeClient(docs=[])).get(1)

    assert exc.value.status_code == 404


# --- on approval --------------------------------------------------------


def test_on_approval_lists_tasks_with_document_and_steps_sorted_by_waiting():
    fresh = task_row(task_id=90000, attachments=(15300,), Started="2026-06-04T08:00:00+04:00")
    docs = [act_row(), act_row(doc_id=15300, Name="Проект №26-050")]
    assignments = ASSIGNMENTS + [
        {"Id": 3, "Subject": "Согласуйте: Проект №26-050", "Created": "2026-06-04T09:00:00+04:00", "Deadline": "2026-06-10T09:00:00+04:00", "Performer": {"Name": "Иванов И.И."}, "Task": {"Id": 90000}}
    ]
    client = FakeClient(docs=docs, tasks=[fresh, task_row()], assignments=assignments)

    result = service(client).on_approval()

    assert [item.task_id for item in result.items] == [82294, 90000]
    assert result.items[0].document.id == 15213
    assert result.items[1].document.id == 15300
    assert result.items[1].current_steps[0].performer == "Иванов И.И."
    task_call = next(kw for es, kw in client.calls if es == "ILegalActsApprovalTasks")
    assert task_call["filter_"] == "Status eq 'InProcess'"
    assignment_call = next(kw for es, kw in client.calls if es == "IAssignments")
    assert "Task/Id eq 90000" in assignment_call["filter_"] and "Task/Id eq 82294" in assignment_call["filter_"]
    assert "Status eq 'InProcess'" in assignment_call["filter_"]


def test_on_approval_filters_by_curator_and_waiting_days():
    client = FakeClient()

    result = service(client).on_approval(curator_id=77, min_days_waiting=5)

    task_call = next(kw for es, kw in client.calls if es == "ILegalActsApprovalTasks")
    assert task_call["filter_"] == "Status eq 'InProcess' and Curator/Id eq 77"
    assert [item.task_id for item in result.items] == [82294]
    assert service(FakeClient()).on_approval(min_days_waiting=30).items == []


def test_on_approval_empty():
    result = service(FakeClient(tasks=[])).on_approval()

    assert result.items == [] and result.total == 0


class FakeRelations:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail

    def get_rows(self, document_id):
        if self.fail:
            raise DirectumError("нет функций", 404)
        return self.rows

    def item(self, row):
        return {"id": row["RelatedId"], "name": row["RelatedName"], "kind": row.get("RelatedKind"),
                "registration_number": row.get("RegistrationNumber"), "registration_date": None,
                "created": "2026-10-14", "has_text": True, "url": f"https://rx/doc/{row['RelatedId']}"}


def rel_row(related, name, group, direction="From"):
    return {"RelatedId": related, "RelatedName": f"Документ {related}", "RelationName": name, "GroupTitle": group,
            "Direction": direction, "RelatedKind": "Простой документ"}


def test_get_uses_confirmed_relations():
    project = act_row(doc_id=16789, RegNumberLegalAct="От 14.10.2026 № 001/26", HasRelations=True)
    adopted = act_row(doc_id=16808, kind="Правовой акт", RegistrationNumber="001/26", ProjectId=16789,
                      RegistrationDate="2026-10-14T00:00:00+04:00")
    relations = FakeRelations([
        rel_row(16805, "Simple relation", "Прочие"),
        rel_row(16809, "ConclusionsAndReports", "Заключения и отчеты"),
        rel_row(16900, "Addendum", "Приложения"),
        rel_row(16808, "LegalAct", "Нормативный правовой акт", "To"),
    ])
    client = FakeClient(docs=[project, adopted], tasks=[])

    detail = LegalActsService(client, tz=STAND_TZ, now=lambda: NOW, relations=relations).get(16789)

    assert detail.adopted_act.id == 16808
    assert [(d.id, d.group, d.relation) for d in detail.related] == [
        (16805, "Прочие", "Simple relation"), (16809, "Заключения и отчеты", "ConclusionsAndReports")]
    assert [d.id for d in detail.addenda] == [16900]
    assert detail.possibly_related == [] and detail.relations_note == ""
    assert not any(es == "IOfficialDocuments" for es, _ in client.calls)  # без подбора по названию


def test_get_adopted_act_from_relation_without_project_id():
    project = act_row(doc_id=16789, HasRelations=True)
    adopted = act_row(doc_id=16808, kind="Правовой акт", RegistrationNumber="001/26")
    relations = FakeRelations([rel_row(16808, "LegalAct", "Нормативный правовой акт", "To")])

    detail = LegalActsService(FakeClient(docs=[project, adopted], tasks=[]), tz=STAND_TZ, now=lambda: NOW,
                              relations=relations).get(16789)

    assert detail.adopted_act.id == 16808 and detail.related == []


def test_get_falls_back_when_relations_unavailable():
    project = act_row(doc_id=16789, Subject="О региональном стандарте максимально допустимой доли расходов граждан",
                      HasRelations=True, Created="2026-10-14T13:48:22+04:00")
    detail = LegalActsService(FakeClient(docs=[project], tasks=[]), tz=STAND_TZ, now=lambda: NOW,
                              relations=FakeRelations(fail=True)).get(16789)

    assert detail.related == [] and "не отдаёт" in detail.relations_note
