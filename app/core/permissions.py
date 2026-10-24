"""Permission codes and the default roles created for every new organization.

Permissions are checked in the service layer (not only the UI), so a request
from any client is subject to the same rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Forbidden

PERMISSIONS: dict[str, str] = {
    "items.read": "عرض الأصناف",
    "items.write": "إضافة وتعديل الأصناف",
    "items.import": "استيراد الأصناف",
    "suppliers.read": "عرض الموردين",
    "suppliers.write": "إضافة وتعديل الموردين",
    "purchases.read": "عرض فواتير الشراء",
    "purchases.create": "إدخال فواتير الشراء",
    "purchases.edit": "مراجعة وتعديل فواتير الشراء",
    "purchases.approve": "اعتماد فواتير الشراء",
    "purchases.cancel": "إلغاء فاتورة معتمدة",
    "inventory.read": "عرض المخزون",
    "inventory.adjust": "تسويات المخزون",
    "inventory.count": "تسجيل الجرد",
    "costs.read": "عرض التكلفة",
    "users.manage": "إدارة المستخدمين",
    "settings.manage": "إدارة الإعدادات",
    "audit.read": "عرض سجل العمليات",
    "safety.read": "عرض تنبيهات السلامة الدوائية",
    "safety.manage": "إنشاء وتعطيل قواعد السلامة",
    "safety.approve": "اعتماد قواعد السلامة وربط المواد الفعالة",
    "safety.override": "تجاوز تنبيه سلامة مرتفع الخطورة بسبب مكتوب",
    "safety.override_critical": "تجاوز تنبيه حرج (بعد مراجعة مختص)",
}

DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "owner": ("المالك", list(PERMISSIONS)),
    "pharmacist": ("صيدلي", [
        "items.read", "items.write", "suppliers.read", "purchases.read",
        "purchases.create", "purchases.edit", "purchases.approve",
        "inventory.read", "costs.read", "safety.read", "safety.override", "inventory.count",
    ]),
    "assistant": ("مساعد", [
        "items.read", "suppliers.read", "purchases.read", "purchases.create",
        "purchases.edit", "inventory.read", "safety.read", "inventory.count",
    ]),
}


@dataclass(frozen=True)
class Context:
    """Who is acting, for which organization and branch. Every service call
    takes one; queries are always filtered by ctx.org_id."""
    user_id: str
    org_id: str
    branch_id: str | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    ip: str | None = None

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if perm not in PERMISSIONS:
            raise ValueError(f"unknown permission {perm}")
        if perm not in self.permissions:
            raise Forbidden(f"ليس لديك صلاحية: {PERMISSIONS[perm]}", details={"permission": perm})
