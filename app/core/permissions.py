"""Permission codes and the default roles created for every new organization.

Permissions are checked in the service layer (not only the UI), so a request
from any client is subject to the same rules.

Effective permissions of a person = (role defaults ∪ grants) − denies, where
grants/denies are per-person exceptions the owner sets. The owner role always
has everything and can never be restricted.

A person who lacks a permission can still do the action if an approver (the
owner or a manager) types his PIN on the same screen: the request then carries
the approver, `require()` accepts the approver's permissions, and the audit row
records who approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import Forbidden

PERMISSIONS: dict[str, str] = {
    "items.read": "عرض الأصناف",
    "items.write": "إضافة وتعديل الأصناف",
    "items.price": "تحديث سعر الجمهور",
    "items.import": "استيراد الأصناف",
    "items.no_return": "تحديد الأقسام التي لا يُقبل إرجاعها",
    "items.hidden": "رؤية وبيع الأصناف المخفية",
    "items.groups": "تحديد المجموعات العلاجية (البدائل)",
    "suppliers.read": "عرض الموردين",
    "suppliers.write": "إضافة وتعديل الموردين",
    "suppliers.pay": "تسجيل دفع للموردين",
    "purchases.read": "عرض فواتير الشراء",
    "purchases.create": "إدخال فواتير الشراء",
    "purchases.edit": "مراجعة وتعديل فواتير الشراء",
    "purchases.approve": "اعتماد فواتير الشراء وإضافتها للمخزون",
    "purchases.cancel": "إلغاء فاتورة شراء معتمدة",
    "inventory.read": "عرض المخزون",
    "inventory.adjust": "تسويات المخزون",
    "inventory.count": "تسجيل الجرد",
    "costs.read": "عرض التكلفة والربح",
    "users.manage": "المستخدمون والصلاحيات",
    "settings.manage": "الإعدادات والنسخ الاحتياطي والتحديث",
    "payroll.manage": "الحضور والرواتب والسلف",
    "audit.read": "عرض سجل العمليات",
    "sales.read": "فتح فواتير البيع",
    "sales.create": "البيع (البائع)",
    "sales.totals": "عرض إجمالي المبيعات وفواتير كل الموظفين",
    "sales.below_cost": "بيع تحت سعر التكلفة",
    "sales.void": "إلغاء فاتورة بيع",
    "sales.return": "مرتجع فاتورة باعها بنفسه",
    "sales.return_others": "مرتجع فاتورة باعها شخص آخر",
    "sales.return_late": "مرتجع بعد مدة المرتجع",
    "sales.return_restricted": "مرتجع أصناف لا ترتجع (ثلاجة واستعمال شخصي)",
    "customers.manage": "إدارة العملاء والآجل",
    "contracts.manage": "التعاقدات وخصوماتها",
    "cash.close": "إغلاق الشيفت (عدّ الخزينة)",
    "cash.see_expected": "معرفة المتوقع في الخزينة",
    "balances.correct": "تصحيح أرصدة الموردين والعملاء بعد النقل (بسبب مكتوب)",
    "cash.manage": "الصرف والإيداع من الخزينة وفتحها",
    "expenses.record": "تسجيل مصروف (ينتظر الموافقة)",
    "expenses.approve": "اعتماد المصروفات",
    "notifications.read": "استلام الإشعارات",
    "safety.read": "عرض تنبيهات السلامة الدوائية",
    "safety.manage": "إنشاء وتعطيل قواعد السلامة",
    "safety.approve": "اعتماد قواعد السلامة وربط المواد الفعالة",
    "safety.override": "تجاوز تنبيه سلامة مرتفع الخطورة بسبب مكتوب",
    "safety.override_critical": "تجاوز تنبيه حرج (بعد مراجعة مختص)",
}

# Permission groups, in the order the users screen shows them.
GROUPS: list[tuple[str, list[str]]] = [
    ("البيع", ["sales.create", "sales.read", "sales.below_cost", "sales.void", "customers.manage", "contracts.manage"]),
    ("المرتجع", ["sales.return", "sales.return_others", "sales.return_late", "sales.return_restricted"]),
    ("ما يراه", ["sales.totals", "costs.read", "cash.see_expected", "notifications.read", "audit.read"]),
    ("الشراء والأصناف", ["purchases.create", "purchases.read", "purchases.edit", "purchases.approve",
                         "purchases.cancel", "items.read", "items.write", "items.price", "items.import", "items.no_return", "items.hidden",
                         "items.groups", "suppliers.read", "suppliers.write", "suppliers.pay"]),
    ("المخزون", ["inventory.read", "inventory.count", "inventory.adjust"]),
    ("النقدية", ["cash.close", "cash.manage", "expenses.record", "expenses.approve", "balances.correct"]),
    ("السلامة الدوائية", ["safety.read", "safety.override", "safety.override_critical",
                          "safety.manage", "safety.approve"]),
    ("الإدارة", ["users.manage", "settings.manage", "payroll.manage"]),
]
assert sorted(p for _, ps in GROUPS for p in ps) == sorted(PERMISSIONS), "every permission in one group"

_EMPLOYEE = ["items.read", "suppliers.read", "purchases.read", "purchases.create", "purchases.edit",
             "inventory.read", "inventory.count", "safety.read", "sales.read", "sales.create",
             "sales.return", "cash.close", "expenses.record"]

# code -> (display name, permissions, default max discount in basis points)
DEFAULT_ROLES: dict[str, tuple[str, list[str], int]] = {
    "owner": ("المالك", list(PERMISSIONS), 10000),
    "manager": ("مدير", _EMPLOYEE + [
        "items.write", "items.price", "items.hidden", "suppliers.write", "suppliers.pay", "purchases.approve", "inventory.adjust",
        "sales.totals", "customers.manage", "cash.see_expected", "cash.manage", "notifications.read",
        "audit.read", "safety.override", "expenses.approve", "balances.correct"], 0),
    "pharmacist": ("صيدلي", _EMPLOYEE + [
        "items.write", "purchases.approve", "customers.manage", "safety.override"], 0),
    "assistant": ("مساعد", list(_EMPLOYEE), 0),
}
ROLE_ORDER = ["owner", "manager", "pharmacist", "assistant"]
APPROVER_ROLES = ("owner", "manager")
# bump when DEFAULT_ROLES changes: system roles of existing pharmacies are re-synced once
ROLES_VERSION = "2026-11-21a"


@dataclass(frozen=True)
class Context:
    """Who is acting, for which organization and branch. Every service call
    takes one; queries are always filtered by ctx.org_id."""
    user_id: str
    org_id: str
    branch_id: str | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    ip: str | None = None
    role_code: str | None = None
    max_discount_bp: int = 0
    approver_id: str | None = None
    approver_permissions: frozenset[str] = field(default_factory=frozenset)
    approver_discount_bp: int = 0

    @property
    def is_owner(self) -> bool:
        return self.role_code == "owner"

    def can(self, perm: str) -> bool:
        """What the person himself may see/do. Visibility never comes from an approver."""
        return perm in self.permissions

    def allowed(self, perm: str) -> bool:
        return perm in self.permissions or perm in self.approver_permissions

    @property
    def discount_limit_bp(self) -> int:
        return max(self.max_discount_bp, self.approver_discount_bp)

    def require(self, perm: str) -> None:
        if perm not in PERMISSIONS:
            raise ValueError(f"unknown permission {perm}")
        if not self.allowed(perm):
            raise Forbidden(f"ليس لديك صلاحية: {PERMISSIONS[perm]}", code="approval_required",
                            details={"permission": perm, "label": PERMISSIONS[perm]})
