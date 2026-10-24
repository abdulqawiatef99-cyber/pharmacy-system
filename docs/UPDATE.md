# تحديث البرنامج على جهاز الصيدلية

## مرة واحدة: مفتاح القراءة من GitHub
المستودع Private، فالجهاز محتاج مفتاح **للقراءة بس**:
1. github.com ← الصورة الشخصية ← **Settings** ← **Developer settings** ← **Personal access tokens** ← **Fine-grained tokens** ← **Generate new token**.
2. الاسم: `pharmacy-pc-update`. المدة: سنة. **Repository access: Only select repositories** ← `pharmacy-system`.
3. **Permissions ← Repository permissions ← Contents: Read-only** (وسيب الباقي No access).
4. انسخ المفتاح واحفظه في الملف: `C:\Users\<اسم المستخدم>\.pharmacy\github_token.txt` (سطر واحد).
   - الملف **برّا فولدر البرنامج**: مش بيترفع، ومش بيدخل في النسخ الاحتياطية.
   - لو المفتاح اتسرب: امسحه من نفس الصفحة على GitHub واعمل واحد جديد.

## كل مرة
اقفل شاشة البرنامج، وبعدين دبل كليك على `scripts\windows\update.bat`. الأمر بيعمل بالترتيب:
1. يشوف آخر نسخة على GitHub، ولو البرنامج محدّث يقول كده ويخرج.
2. **نسخة احتياطية متأكد منها للبيانات** في `backups\`؛ لو فشلت، التحديث بيتلغي.
3. نسخة من البرنامج الحالي في `backups\code-before-update-*.zip` (للرجوع).
4. ينزّل النسخة الجديدة ويتأكد إنها سليمة قبل ما يكتب أي ملف، وبعدين يحطها مكان القديمة. **فولدرات `data` و`backups` و`.venv` ما بتتلمسش.**
5. يثبّت المكتبات (لو قايمتها اتغيرت)، ويطبّق تحديثات قاعدة البيانات، ويعمل `verify` و`selftest --quick`.

## الرجوع لنسخة قديمة
```
python manage.py update --from-zip backups\code-before-update-YYYYMMDD-HHMMSS.zip
```
ولو البيانات نفسها محتاجة ترجع: `python manage.py restore backups\pharmacy-backup-....zip --yes`.

## الحالة
- اتختبر هنا: التحديث من ملف zip (بيانات سليمة، ورفض الملفات التالفة والمسارات الخطرة، والرجوع).
- **ما اتختبرش لسه:** التنزيل الفعلي من GitHub بالمفتاح (محتاج جهاز الصيدلية).
