@echo off
cd /d C:\Users\SubratoBiswas\trinamix-conversion-workbench
echo ===== push_geo started %date% %time% ===== > push_geo.log
echo --- staging the COMPLETE Batch A slice (all 10 files incl. new package __init__ files) --- >> push_geo.log 2>&1
git add backend/app/domain/geo/__init__.py backend/app/domain/geo/country.py backend/app/domain/phone/__init__.py backend/app/domain/phone/parse.py backend/app/domain/rules/library/geo_ops.py backend/app/domain/rules/library/phone_ops.py backend/app/domain/rules/registry.py backend/app/services/deterministic.py backend/app/transformations/engine.py backend/tests/unit/test_rules.py >> push_geo.log 2>&1
echo --- staged files --- >> push_geo.log 2>&1
git status --porcelain >> push_geo.log 2>&1
git commit -m "refactor(rules): migrate geo/phone rule types (Batch A) to strategies" -m "Migrate COUNTRY_ISO2, CITY_COUNTRY_KEY, PHONE_PART, PHONE_STRIP_AREA out of engine._apply_one_rule. Relocate the ISO country table to app/domain/geo/country.py (services.deterministic re-imports it; hdl_output_service unchanged) and the libphonenumber split to app/domain/phone/parse.py. engine.py 642->266 lines. Behaviour-preserving: 77-case differential battery byte-identical, country table verified identical (119 entries), 25/25 unit tests pass. 34/40 rule types now on the registry." >> push_geo.log 2>&1
echo --- HEAD after commit --- >> push_geo.log 2>&1
git log --oneline -3 >> push_geo.log 2>&1
echo --- confirm new modules tracked at HEAD --- >> push_geo.log 2>&1
git ls-tree -r --name-only HEAD | findstr "geo_ops phone_ops geo/country phone/parse" >> push_geo.log 2>&1
echo ===== pushing HEAD to origin main ===== >> push_geo.log 2>&1
git push origin HEAD:main >> push_geo.log 2>&1
echo EXITCODE=%errorlevel% >> push_geo.log 2>&1
echo ===== done ===== >> push_geo.log 2>&1
type push_geo.log
