# GitHub (مستودع خاص)

- المستودع: `abdulqawiatef99-cyber/pharmacy-system` (Private)، متصل ومرفوع بتاريخه كامل منذ 2026-10-24.
- **الفروع:** `main` = قناة تحديث الصيدلية (زرار "تحديث" في البرنامج بيسحب `main`، وأي push عليه بينشر `installer-latest`)،
  فمبيتحركش غير بموافقة المالك. `develop` = الدمج اليومي. فروع الشغل: `claude/<issue>-…` و`codex/<issue>-…`. القواعد في `AGENTS.md`.
- **CI:** `.github/workflows/ci.yml` (اختبارات Linux + فحص الأسرار على كل PR؛ Windows على PRs لـ main وعلى develop) و
  `windows-installer.yml` (بناء المثبّت؛ النشر من `main` بس، والتشغيل اليدوي على فرع تاني بيطلع artifact من غير نشر).
- **ممنوع** ترفع `data/` أو `backups/` أو فواتير أو بيانات حقيقية أو ملفات تصدير E-Stock؛ `.gitignore` و`scripts/check_secrets.py` بيمنعوها
  (فعّل الحماية على جهازك: `python scripts/check_secrets.py --install`).
- مفتاح `ANTHROPIC_API_KEY` ومفتاح التحديث في متغيرات البيئة أو ملف على الجهاز بس، عمره ما يتكتب في الكود.
- الإصدارات: tag بالشكل `v0.4.0` عند كل دمج معتمد في `main`.
