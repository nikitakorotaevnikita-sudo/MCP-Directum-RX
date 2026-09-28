from typing import Any

from src.models.schemas import EmployeeSummary

# Защита от циклов и гигантских деревьев оргструктуры.
MAX_DEPARTMENTS = 200
MAX_EMPLOYEES = 1000
# Сколько id подразделений собирать в одно условие «or» (длина URL).
FILTER_CHUNK = 20


def _chunks(items: list[int], size: int = FILTER_CHUNK):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _or(field: str, ids: list[int]) -> str:
    return "(" + " or ".join(f"{field}/Id eq {int(item)}" for item in ids) + ")"


class SubordinationService:
    """Подчинённые текущего пользователя: сотрудники подразделений, где он руководитель (Manager),
    включая вложенные подразделения (HeadOffice). Результат кешируется на время жизни сервиса (один вызов тула)."""

    def __init__(self, client: Any, current_user_service: Any):
        self.client = client
        self.current_user_service = current_user_service
        self._managed: list[int] | None = None

    def managed_department_ids(self) -> list[int]:
        if self._managed is not None:
            return self._managed
        me = self.current_user_service.get_current_user()
        roots = self.client.query(
            "IDepartments",
            filter_=f"Manager/Id eq {int(me.id)} and Status eq 'Active'",
            select="Id",
            top=MAX_DEPARTMENTS,
        )
        self._managed = self.department_tree([int(row["Id"]) for row in roots])
        return self._managed

    def department_tree(self, root_ids: list[int]) -> list[int]:
        """Подразделения и все вложенные (по HeadOffice), в порядке обхода в ширину."""
        found: list[int] = []
        seen: set[int] = set()
        frontier = [int(item) for item in root_ids]
        while frontier and len(found) < MAX_DEPARTMENTS:
            fresh = [item for item in frontier if item not in seen]
            seen.update(fresh)
            found.extend(fresh)
            children: list[int] = []
            for chunk in _chunks(fresh):
                rows = self.client.query(
                    "IDepartments",
                    filter_=f"{_or('HeadOffice', chunk)} and Status eq 'Active'",
                    select="Id",
                    top=MAX_DEPARTMENTS,
                )
                children.extend(int(row["Id"]) for row in rows)
            frontier = [item for item in children if item not in seen]
        return found[:MAX_DEPARTMENTS]

    def find_departments(self, query: str, top: int = 10) -> list[dict[str, Any]]:
        text = query.strip()
        if text.isdigit():
            filter_ = f"Id eq {int(text)}"
        else:
            filter_ = "contains(Name,'{}') and Status eq 'Active'".format(text.replace("'", "''"))
        rows = self.client.query("IDepartments", filter_=filter_, select="Id,Name", top=top)
        return [{"id": int(row["Id"]), "name": row.get("Name") or ""} for row in rows]

    def is_subordinate(self, employee_id: int) -> bool:
        managed = self.managed_department_ids()
        if not managed:
            return False
        rows = self.client.query(
            "IEmployees", filter_=f"Id eq {int(employee_id)}", select="Id", expand="Department($select=Id)", top=1
        )
        department = rows[0].get("Department") if rows else None
        return isinstance(department, dict) and department.get("Id") in managed

    def can_view(self, employee_id: int) -> bool:
        """Себя видит любой; остальных — только руководитель их подразделения (или вышестоящего)."""
        if int(employee_id) == int(self.current_user_service.get_current_user().id):
            return True
        return self.is_subordinate(employee_id)

    def subordinates(self, department_ids: list[int] | None = None) -> list[EmployeeSummary]:
        managed = self.managed_department_ids()
        return self.employees_of([item for item in (department_ids or managed) if item in managed])

    def employees_of(self, department_ids: list[int]) -> list[EmployeeSummary]:
        """Активные сотрудники подразделений. Доступ проверяет вызывающий код."""
        people: list[EmployeeSummary] = []
        for chunk in _chunks(list(department_ids)):
            rows = self.client.query(
                "IEmployees",
                filter_=f"{_or('Department', chunk)} and Status eq 'Active'",
                select="Id,Name,Status",
                expand="Department($select=Id,Name)",
                top=MAX_EMPLOYEES,
            )
            for row in rows:
                department = row.get("Department") if isinstance(row.get("Department"), dict) else {}
                people.append(
                    EmployeeSummary(
                        id=int(row["Id"]),
                        name=row.get("Name") or "",
                        status=row.get("Status"),
                        department=department.get("Name"),
                    )
                )
            if len(people) >= MAX_EMPLOYEES:
                break
        return people[:MAX_EMPLOYEES]
