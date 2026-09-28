from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

NO_EMPLOYEE_ACCESS = (
    "Нет доступа к поручениям этого сотрудника: их видят сам сотрудник, его руководитель "
    "(руководитель подразделения или вышестоящего) и администраторы Directum RX."
)


def is_admin(services: Any) -> bool:
    """Ошибка проверки — «не администратор»: доступ не расширяем при сбое."""
    try:
        return bool(services.admin_access.is_admin())
    except Exception:
        return False


def can_view_employee(services: Any, employee_id: int) -> bool:
    if is_admin(services):
        return True
    try:
        return bool(services.subordination.can_view(employee_id))
    except Exception:
        return False


def ensure_can_view_employee(services: Any, employee_id: int) -> None:
    if not can_view_employee(services, employee_id):
        raise ToolError(NO_EMPLOYEE_ACCESS)
