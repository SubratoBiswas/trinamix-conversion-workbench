@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_lookup started %date% %time% ===== > push_lookup.log
echo --- staging Batch B (final): the 4 files --- >> push_lookup.log 2>&1
git add backend/app/domain/rules/library/lookup_ops.py backend/app/domain/rules/registry.py backend/app/transformations/engine.py backend/tests/unit/test_rules.py >> push_lookup.log 2>&1
echo --- staged files --- >> push_lookup.log 2>&1
git status --porcelain >> push_lookup.log 2>&1
git commit -m "refactor(rules): migrate index-backed lookup types (Batch B) — engine is now pure dispatch" -m "Migrate SELF_LOOKUP, CROSS_CONVERSION_LOOKUP, GROUP_FIRST_FLAG, SEQUENCE, CROSSWALK_LOOKUP into app/domain/rules/library/lookup_ops.py (each reads a per-generation ctx index). engine._apply_one_rule is now pure registry dispatch — the entire if/elif chain is gone and every domain-helper import with it (engine.py 266->91 lines). Migration complete: 39 rule types on the registry, engine owns zero transformation logic. Behaviour-preserving: 35-case differential battery byte-identical, 32/32 unit tests pass." >> push_lookup.log 2>&1
echo --- HEAD after commit --- >> push_lookup.log 2>&1
git log --oneline -3 >> push_lookup.log 2>&1
echo --- confirm lookup_ops tracked at HEAD --- >> push_lookup.log 2>&1
git ls-tree -r --name-only HEAD | findstr lookup_ops >> push_lookup.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_lookup.log 2>&1
git push origin HEAD:main >> push_lookup.log 2>&1
echo EXITCODE=%errorlevel% >> push_lookup.log 2>&1
echo ===== done ===== >> push_lookup.log 2>&1
type push_lookup.log
