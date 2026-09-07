# Проверенные внешние сведения на 2026-09-07

- OR-Tools PyPI 9.15.6755:
  https://pypi.org/project/ortools/9.15.6755/
  На странице присутствует CPython 3.13 Linux aarch64 wheel.

- OSRM GitHub package 26.8.0-debian:
  https://github.com/Project-OSRM/osrm-backend/pkgs/container/osrm-backend/1088233374?tag=26.8.0-debian
  Публикуются linux/amd64 и linux/arm64.

- OSRM repository / MLD Docker pipeline:
  https://github.com/Project-OSRM/osrm-backend

- openrouteservice API restrictions:
  https://openrouteservice.org/restrictions/
  Directions: до 50 waypoints; Matrix: до 3500 origin×destination элементов.

- DaData clean/geocode API:
  https://dadata.ru/api/geocode/
  https://dadata.ru/api/clean/address/

Внешние тарифы и API могут меняться. Перед production-развёртыванием агент должен
сверить документацию, если провайдер возвращает ошибку несовместимости.
