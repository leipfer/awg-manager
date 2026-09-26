# Как внести вклад

Спасибо за интерес к проекту!

## Запуск из исходников

```bash
git clone https://github.com/leipfer/awg-manager.git
cd awg-manager
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-ayatanaappindicator3-0.1
AWG_HELPER=$PWD/helper/awg-helper PYTHONPATH=$PWD/src python3 -m awgmanager.app
```

## Сборка пакета

```bash
./build.sh          # версия берётся из src/awgmanager/app.py
./build.sh 1.2.0    # или указывается явно
```

## Тесты

```bash
python3 -m unittest discover -s tests -v
```

## Стиль кода

- PEP 8, отступ 4 пробела, строки до 100 символов
- Комментарии и строки интерфейса — на русском
- Привилегированный код живёт только в `helper/`; клиент никогда
  не вызывает `systemctl`/`ip` напрямую
- Любое новое действие хелпера обязано валидировать имя интерфейса
  через `valid_iface()`

## Безопасность

Если нашли уязвимость, не открывайте публичный issue — напишите
через GitHub Security Advisories.
