# HANDOFF — الحالة الحالية

آخر تحقق: 2026-10-31 · Claude · أساس الملخص: `develop` @ `7190880`. الأولويات والأدوار: [LAUNCH_PLAN.md](LAUNCH_PLAN.md).
قواعد العمل وبداية/نهاية الجلسة: [AGENTS.md](../AGENTS.md). البوابات والنسبة: [STATUS.md](STATUS.md).
تفاصيل المهمة تُقرأ عند الحاجة من الروابط تحت؛ السجل القديم مش مطلوب لبداية الجلسة.

## الحالة المثبتة

- مفيش PR مفتوحة أو Issue كود `launch-critical` وقت التحقق. المفتوح للإطلاق: [#18](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18) ← [#19](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/19) ← [#20](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/20).
- إصلاحات تجربة الجهاز اتدمجت: #35، #37، #39، #42، #44، #46، #49؛ إعادة التحقق على الأجهزة لسه مطلوبة.
- مراجعة الإصدار [#68](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/68) اتعملت بالفعل؛ العيب المؤكد [#72](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/72) اتقفل بإصلاح [#73](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/73)، مدمج @ `c9906a9`. المرتجع القديم يحافظ على المدفوع 99.99؛ السجل يثبت 535 اختبار بلا skip ومراجعة Codex سليمة على `de44f90`؛ [CI ناجح](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41031438683). لا تعيد المراجعة أو الإصلاح من الصفر.
- **المثبّت الحالي: [Windows installer run 75](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41237493804)**، artifact `PharmacySetup` (id 13055634945، digest الـ zip `sha256:3a07be7a…ae18f`)، كود `develop` @ `7190880`؛ اختبارات Windows + smoke + Inno ناجحة، النشر متخطّى. يشمل #85 و#81 و#84 و#80. run 72 (`c9906a9`) بقى قديم.
- CI على `develop` @ `7190880` أخضر linux + windows ([run 137](https://github.com/abdulqawiatef99-cyber/pharmacy-system/actions/runs/41236971344)).
- النسبة المسجلة **40%، كل البوابات L1**؛ لا دليل قبول جديد. أرقام الاختبارات والمثبّت القديمة في STATUS تاريخية؛ الدليل الأحدث فوق. لابتوب البيت جهاز Windows حقيقي: حسب تعريف L2 في STATUS ممكن نتايجه تبقى دليل L2 للبوابات اللي مالهاش عمود أجهزة (G2–G6) **بعد تأكيد المالك بس**؛ G7/G8 لازم جهاز الصيدلية.
- CI: فشل Windows على `0bcba94` (#75) **متذبذب** مش عيب: نفس الكود نجح على `2f47d6c` (#78)، وده تاني مرة النهارده (#67 كمان). من بعد الـ PR ده فشل خطوة Windows في CI بيطلع كـ annotation فيها الاختبار اللي فشل (اللوج نفسه مقفول من جلسات الوكلاء). أول مرة يتكرر: اقرا الـ annotation وصلّح الاختبار المتذبذب بدليل.

## نتيجة جهاز الصيدلية (2026-10-30 بالليل) — الملخص الكامل في [تعليق #18](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6533037626)
- run 72 اتثبت فوق 0.3.0 والبيانات سليمة. ✅ الطباعة المباشرة بالعربي على POS-80C، والدرج (لوحده ومع البيع النقدي)، والإيصال بالمبالغ كاملة، والطباعة من المتصفح، والبحث وقت الكتابة، والمسح السريع (#48).
- ❌ [#85](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/85) **launch-critical**: المتصفح بيشغّل `app.js` قديم بعد التثبيت (مفيش Cache-Control على `/static`)، فالخطوة 9 فشلت بالكود القديم. ❌ [#84](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/84): مقاس الملصق غلط (ملصق على اتنين) + تصميم E-Stock. الخطوتين 11 و12 ماتعملوش (مفيش shell).
- اكتشاف: القارئ HH360 بيبعت **F12** مع الباركود (الحل في #81). القارئ 1D بس؛ في قارئ 2D في الصيدلية يتجرب.

## اتعمل (2026-10-30/31) — 4 PRs + إصلاحين CI، كلها بمراجعة Codex
- #85 ← [#88](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/88): نسخة `app.js` بالـ hash + `no-cache` على `/static`.
- #81 ← [#89](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/89): إيصال اختياري (F4، قاعدة مبلغ)، درج مستقل مرة واحدة لكل بيع (قبل/مع/بعد)، F8 بسبب ومسجّل في تقرير الوردية، F12 للقارئ، 12/24 ساعة، مقدمة/ضريبي/سجل تجاري، نسخ. **لسه:** باركود رقم الفاتورة + قالب جدول E-Stock.
- #84 ← [#90](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/90): مقاس مخصص، حقول E-Stock كـ toggles، باركود ≥0.25 مم، شبكة A4، معاينة. كل الحقول بتتسع على 38×13.
- #80 ← [#91](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/91): أوامر الصيانة متعملش قاعدة فاضية؛ الافتراضي على ويندوز `%LOCALAPPDATA%\PharmacyPlatform\data`.
- Windows CI على develop وقع مرتين من اختبارات جديدة (مش من التطبيق) واتصلّح: [#92](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/92)، [#93](https://github.com/abdulqawiatef99-cyber/pharmacy-system/pull/93). درس: شغّل `scripts/validate_local_release.py` محليًا قبل أي اختبار CLI/مسارات.

## التالي بالضبط
1. المالك: مقاس بكرة الملصق بالظبط (يتكتب في الإعدادات ← شكل الملصق ← مقاس آخر).
2. إعادة تجربة #18 بـ run 75 حسب [قائمة التعليق](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6548642933) (خطوة 9، إيصال/درج، ملصق، قارئ). كل فشل = Issue `launch-critical`.
3. بعدها #19 ثم #20 بموافقة المالك. بعد الإطلاق: [#86](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/86).

## المرجع القديم للتجربة
- [DEVICE_RUNBOOK.md](DEVICE_RUNBOOK.md) (A = لابتوب، B = صيدلية) — التقسيم المحدّث في تعليق #18 أعلاه.

- تعليمات لابتوب البيت: [التعليق المرجعي](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/18#issuecomment-6520210512). لا نتائج اختبار لابتوب مسجلة وقت التحقق.
- قبل أي تنفيذ محلي: `hostname` لازم يكون `DESKTOP-0DCSIC2`؛ أي جهاز تاني، خصوصًا جهاز الصيدلية `DESKTOP-5JVH3QD`، توقّف. افحص Windows/RAM/المساحة والعمليات ومساري التثبيت في التعليق والبورت 8000؛ لو فيه تثبيت أو البورت مشغول توقّف. المثبّت بورتُه ثابت.
- بعد موافقة المالك على التثبيت: نسخة تجربة per-user بدون admin، بيانات وهمية فقط. الترتيب: تثبيت/تشغيل ← بيع ومرتجع (99.99 القديمة) وخصم ووردية وخزينة ← مخزون/تسوية ← نسخ/استعادة ← تحديث/رجوع ← إعادة تشغيل وثبات البيانات ← `preflight` لنسخة وهمية. سجّل كل مرحلة في #18 بعنوان **home laptop, not pharmacy PC**؛ الأجهزة غير المتوصلة = «لم تُختبر».
- تفاصيل القبول وأوامر R8: [WINDOWS_ACCEPTANCE.md §0، R1–R10](WINDOWS_ACCEPTANCE.md). مرشح التحديث `b6329815b049eac128d6fd97cda539e2cbfa2458`: 0.4.0rc1 → 0.4.0rc2 → rollback إلى rc1، البيانات ثابتة.
- إعادة تجربة جهاز الصيدلية: [جدول الـ12 خطوة المحفوظ](../archive/HANDOFF_2026-10-30_before_compaction.md). استخدمه لتفاصيل إعادة الاختبار فقط؛ الحالات التاريخية في الأرشيف ليست مهام جديدة. G6/G7 لا تصل L2 إلا بدليل نجاح **كل R1–R10**، ومنها الملصقات والتحديث/الرجوع؛ نجاح إصلاحات الواجهة وحده مش كفاية.
- أجهزة الصيدلية المعروفة: إيصال `POS-80C` USB002، الدرج متوصل بيها؛ ملصقات `Xprinter XP-235B` USB004؛ قارئ `HH360 Linear Imager` USB ككيبورد. موديل استيكر الإيصال لسه اختياري. لازم اختبار فعلي للطباعة والملصق والقارئ والدرج؛ Windows CI مش بديل.
- أي فشل قبول: Issue مستقلة `launch-critical` مع الدليل. بعدها #19 حسب [OWNER_TEST_PLAN.md](OWNER_TEST_PLAN.md)، ثم #20 بموافقة المالك المكتوبة.

## قيود وقرارات مستمرة

- لا تعديل `main` أو نشر أو دمج في المهمة الحالية؛ PR إلى `develop` فقط، ومراجعة مستقلة وموافقة المالك قبل الدمج. لا ميزات جديدة قبل بوابات الإطلاق.
- لا بيانات حقيقية أو اتصال بأنظمة الصيدلية أو لمس E-Stock/Radmin VPN/تعريف Xprinter أو البرامج والإعدادات الحالية. لا تثبيت أو تغيير إعدادات بدون موافقة المالك؛ لا admin ولا تعطيل Defender/SmartScreen ولا حذف/استبدال ملفات الجهاز.
- الوصول للسحابة لا يثبت وصولًا للابتوب. الجلسة الحالية Linux؛ لا VPN/اتصال لابتوب مهيأ. `git fetch` فشل باتصال proxy؛ موصل GitHub قرأ أحدث develop بنجاح. لا تعدّل نسخة محلية قديمة ولا تحاول اتصالًا غير مصرح به.
- أول انتقال قديم يتم بالمثبّت + نسخة مفحوصة، مش بالتحديث القديم. `preflight` قراءة فقط؛ لو «لا تنفع للنقل» توقّف عن الاستعادة. تغيير المكتبات/migrations يتطلب مثبّت ([UPDATE.md](UPDATE.md)).
- جهاز الصيدلية سبق تثبيت المثبّت عليه وبه بيانات تجربة؛ لا تفترض أنه ما زال `install.bat` فقط، ولا تحذف بياناته تلقائيًا. تحقق محليًا وخطة انتقال آمنة قبل الاستخدام الحقيقي.
- [#12](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/12): الحارس المجاني #53 مدمج؛ ثغرة force-push لنسخة أقدم من الحارس مؤجلة بعد الإطلاق بقرار المالك. مطابقة المتطلبات المستقبلية (#21 في السجل) بعد الإطلاق.

## الأتمتة والتسليم الحالي

- **يدوي حاليًا:** اختبارات Windows/الأجهزة والقبول. **جزئي/تاريخي:** استيقاظ جلسة Claude نفسها ومتابعة CI اتجرب سابقًا؛ ده لا يثبت استمرارها بعد حد الاستخدام.
- **فشل سابقًا:** جلسة مجدولة جديدة بلا وصول للمستودع؛ تنفيذ `@codex` على GitHub (#68/#69) لم يعمل، بينما المراجعات والتنفيذ المباشر عملوا. لا تعد بتكملة تلقائية؛ لا جدولة جديدة في المهمة دي.
- **غير مؤكد:** وصول push للموبايل؛ @mentions من حساب المالك لا يعتمد عليها كتنبيه له.
- مهمة Codex الحالية: اختصار التسليم فقط، فرع `codex/18-current-handoff` من `aef9532`؛ AGENTS والتطبيق بلا تغيير. تحقق: قراءة GitHub ومقارنة الملفات؛ لا اختبارات تطبيق محلية (fetch متعطل). التالي: مراجعة PR وCI وموافقة المالك؛ لم يحدث دمج.
- [السجل الكامل قبل الاختصار](../archive/HANDOFF_2026-10-30_before_compaction.md) و[سجل Codex السابق](../archive/HANDOFF_2026-10-28_codex.md) للتفاصيل التاريخية فقط.
