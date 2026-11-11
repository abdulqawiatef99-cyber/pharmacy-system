# ابدأ من هنا — الباقي على الإطلاق

آخر تحديث: 2026-11-11 · Claude · بقرار المالك. القواعد الكاملة: [AGENTS.md](../AGENTS.md) و[LAUNCH_PLAN.md](LAUNCH_PLAN.md)، والحالة التفصيلية: [HANDOFF.md](HANDOFF.md).

## الهدف
البرنامج يشتغل في الصيدلية في أقرب وقت. أي حاجة مش في الجدول تحت مستنية لبعد الإطلاق وعليها علامة `after-launch` (مفتوحة، ما اتقفلتش ولا اتمسحت).

## المفتوح للإطلاق بالترتيب

| # | المهمة | مين يعمل إيه | تخلص إمتى |
|---|---|---|---|
| 1 ✅ | [#100](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/100) الملصق على طابعة Xprinter مقاس 38×13 بيطبع نازل لتحت وبيسيب ملصق فاضي بين كل اتنين | **اتصلّح** في [PR #110](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/110)، والمالك يبص على الملصق في الزيارة (5) | 5 ملصقات ورا بعض كاملة من غير فاضي |
| 2 ✅ | [#98](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/98) طابعة الإيصال مقفولة: البيع بيقول «اتبعت» من غير تحذير، والدرج بيفتح لوحده بعدين | **اتصلّح** في [PR #111](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/111)، والمالك يقفل الطابعة ويجرب في الزيارة (5) | تحذير واضح، ومفيش إيصال أو درج يشتغل لوحده بعدين |
| 3 ✅ | [#104](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/104) فرع `main` فيه 12 تعديل مش موجودين في `develop` | **اتقرر: الخيار 1** ([تعليق المالك على #104](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/104#issuecomment-6779470362)): `main` يفضل زي ما هو لحد #20 | `main` جاهز يستقبل النسخة المختبرة في #20 |
| 4 ✅ | [#107](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/107) زرار «تحديث» في الإعدادات يشتغل على قناة `develop` لنسخ التجربة | **اتعمل** في [PR #112](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/112)، والمالك يدوس الزرار مرة في الزيارة (5) | تعديل صغير يوصل الجهاز بالزرار من غير تحميل مثبّت يدوي |
| 5 ⬅ **التالي** | [#18](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18) زيارة إعادة التجربة في الصيدلية: القائمة كاملة في [VISIT_18_CHECKLIST.md](VISIT_18_CHECKLIST.md) | **Claude** يشغّل الأوامر، **المالك** يعمل الخطوات بإيده — والصيدلية شغالة والمالك متابع الشاشة، ونسخة احتياطية الأول. المثبّت جاهز: الإصدار التجريبي [`installer-review`](https://github.com/abdulqawiatef99-cyber/pharmacy-system/releases/tag/installer-review) | كل سطر مكتوب «نجح» أو «فشل» بدليل؛ أي فشل = Issue جديدة |
| 6 | [#19](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/19) يوم تشغيل تجريبي ببيانات وهمية | **المالك** يمشي على [DAY19_SCRIPT.md](DAY19_SCRIPT.md) (الترتيب والأرقام المتوقعة)، **Claude** يوجّه ويطابق الأرقام | الخزينة والمخزون والفواتير والتقارير متطابقين، والمالك يوقّع |
| 7 | [#20](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/20) أول إصدار رسمي | **المالك** يوافق كتابةً، **Claude** ينفّذ بعد مواعيد العمل | النسخة على `main` ومتثبتة، ونسخة احتياطية على فلاشة، وE-Stock شغال جنبه (أسبوع مقترح) |

اتعمل كمان 11/11: إصلاح شاشة المرتجع ([#114](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/114)، [PR #115](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/115)) ورفض ملف E-Stock المقطوع ([#116](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/116)، [PR #117](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/117)). تقدير (مش وعد): ليلة تجربة واحدة في الصيدلية (5)، ثم يوم التشغيل (6) والإصدار (7).

## مطلوب منك إنت بس
- دلوقتي: موافقتك على مسح ملفات التثبيت القديمة على GitHub ([#122](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/122)، ببلاش)، وبعدها موافقتك على بناء مثبّت مراجعة واحد.
- الوقت في الصيدلية للخطوات 5 و6 و7.
- اللي #20 لسه محتاجه منك:
  1. قرار اسم المنتج بدل «منصة الصيدلية».
  2. موافقتك المكتوبة على #20.
  3. نسخة احتياطية متأكد منها على فلاشة (لسه ناقصة من زيارة 11/01).
  4. E-Stock يفضل شغال جنب البرنامج حوالي أسبوع بعد الإصدار.

## مش للإطلاق (علامة `after-launch`)
Issues: #96 (التحديث التلقائي الكامل)، #97، #95، #86، #76، #106، #12. PRs: #99 (التحديث التلقائي الكامل)، #102، #83، #77.

## مصطلحات
- **Issue**: كارت مهمة على GitHub. **PR**: طلب دمج تعديل في الكود.
- **`develop`**: فرع التجربة. **`main`**: فرع الصيدلية؛ اللي عليه بيوصل جهاز الصيدلية.
- **CI**: اختبارات أوتوماتيك بتشتغل على كل تعديل؛ «أخضر» يعني نجحت.
