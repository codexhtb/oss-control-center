# ADR-004: Persisted grants и серверный RBAC

**Статус:** принято по текущему коду. **Дата фиксации:** 2026-11-18.

## Решение

Role предлагает default permission preset, а для кастомизированного аккаунта effective permissions читаются из PostgreSQL `user_permissions`. Backend проверяет capability на каждом endpoint; UI скрывает недоступное для удобства, но не служит security boundary.

## Основание и границы

Реализация видна в `app/modules/identity/roles.py` и `access.py`. Legacy/account defaults требуют совместимости и не сводятся к названию `admin`. Мотивация исходного решения отдельно не найдена.
