import re

from src.models.schemas import DirectumUser
from src.services.subordination import SubordinationService

# Дерево: 445 (рук. 63) → 500 → 501; 600 — чужое подразделение.
DEPARTMENTS = {
    445: {"manager": 63, "head": None, "name": "Отдел документационного обеспечения"},
    500: {"manager": 70, "head": 445, "name": "Сектор регистрации"},
    501: {"manager": 71, "head": 500, "name": "Группа архива"},
    600: {"manager": 80, "head": None, "name": "Правовой отдел"},
}
EMPLOYEES = {63: 445, 70: 500, 71: 501, 72: 501, 80: 600, 81: 600}


def _ids(filter_, field):
    return [int(value) for value in re.findall(rf"{field}/Id eq (\d+)", filter_)]


class FakeClient:
    def __init__(self):
        self.calls = []

    def query(self, entity_set, **kwargs):
        self.calls.append((entity_set, kwargs))
        filter_ = kwargs["filter_"]
        if entity_set == "IDepartments" and "Manager/Id eq" in filter_:
            manager = _ids(filter_, "Manager")[0]
            return [{"Id": dep_id, "Name": d["name"]} for dep_id, d in DEPARTMENTS.items() if d["manager"] == manager]
        if entity_set == "IDepartments" and "HeadOffice/Id eq" in filter_:
            heads = set(_ids(filter_, "HeadOffice"))
            return [{"Id": dep_id, "Name": d["name"]} for dep_id, d in DEPARTMENTS.items() if d["head"] in heads]
        if entity_set == "IEmployees" and filter_.startswith("Id eq"):
            employee_id = int(filter_.split("Id eq ")[1].split(" ")[0])
            dep = EMPLOYEES.get(employee_id)
            return [{"Id": employee_id, "Department": {"Id": dep}}] if dep else []
        if entity_set == "IEmployees":
            deps = set(_ids(filter_, "Department"))
            return [
                {"Id": emp_id, "Name": f"Сотрудник {emp_id}", "Department": {"Id": dep, "Name": DEPARTMENTS[dep]["name"]}}
                for emp_id, dep in EMPLOYEES.items()
                if dep in deps
            ]
        raise AssertionError(f"unexpected query {entity_set} {filter_}")


class Me:
    def __init__(self, user_id):
        self.user_id = user_id

    def get_current_user(self):
        return DirectumUser(id=self.user_id, name="Я", login="me")


def service(me=63):
    return SubordinationService(FakeClient(), Me(me))


def test_managed_departments_include_nested():
    svc = service()

    assert svc.managed_department_ids() == [445, 500, 501]
    first = svc.client.calls[0]
    assert first[0] == "IDepartments"
    assert "Manager/Id eq 63" in first[1]["filter_"] and "Status eq 'Active'" in first[1]["filter_"]


def test_managed_departments_cached():
    svc = service()

    svc.managed_department_ids()
    calls = len(svc.client.calls)
    svc.managed_department_ids()

    assert len(svc.client.calls) == calls


def test_non_manager_has_no_departments():
    assert service(me=72).managed_department_ids() == []


def test_is_subordinate():
    svc = service()

    assert svc.is_subordinate(72) is True  # вложенное подразделение 501
    assert svc.is_subordinate(70) is True
    assert svc.is_subordinate(81) is False  # чужой отдел
    assert svc.is_subordinate(999) is False  # нет такого сотрудника


def test_manager_is_not_own_subordinate_but_can_view_self():
    svc = service()

    assert svc.can_view(63) is True


def test_non_manager_skips_employee_lookup():
    svc = service(me=72)

    assert svc.is_subordinate(81) is False
    assert all(call[0] == "IDepartments" for call in svc.client.calls)


def test_subordinates_list_all_employees_of_managed_departments():
    svc = service()

    people = svc.subordinates()

    assert sorted(person.id for person in people) == [63, 70, 71, 72]
    employees_query = next(kw for es, kw in svc.client.calls if es == "IEmployees")
    assert "Status eq 'Active'" in employees_query["filter_"]


def test_subordinates_of_specific_department_must_be_managed():
    svc = service()

    assert sorted(person.id for person in svc.subordinates(department_ids=[501])) == [71, 72]
    assert svc.subordinates(department_ids=[600]) == []


def test_department_tree_from_any_root():
    assert service().department_tree([500]) == [500, 501]


def test_employees_of_departments_without_manager_restriction():
    people = service(me=72).employees_of([600])

    assert sorted(person.id for person in people) == [80, 81]
    assert people[0].department == "Правовой отдел"


def test_find_departments_by_name_or_id():
    class Client(FakeClient):
        def query(self, entity_set, **kwargs):
            if entity_set == "IDepartments" and "contains(Name" in kwargs["filter_"]:
                self.calls.append((entity_set, kwargs))
                return [{"Id": 445, "Name": "Отдел документационного обеспечения"}]
            if entity_set == "IDepartments" and kwargs["filter_"].startswith("Id eq"):
                self.calls.append((entity_set, kwargs))
                return [{"Id": 600, "Name": "Правовой отдел"}]
            return super().query(entity_set, **kwargs)

    svc = SubordinationService(Client(), Me(63))

    assert [d["id"] for d in svc.find_departments("документационного")] == [445]
    assert "Status eq 'Active'" in svc.client.calls[-1][1]["filter_"]
    assert svc.find_departments("600") == [{"id": 600, "name": "Правовой отдел"}]
