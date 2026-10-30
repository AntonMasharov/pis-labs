"""Browser regression scenario and reproducible video report.

Optional tools: pip install playwright imageio-ffmpeg pillow
Use installed Chrome; runtime application itself has no dependencies.
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

SOLUTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOLUTION))
sys.path.insert(0, str(SOLUTION / ".tools"))
if (SOLUTION / ".tools" / "browsers").exists():
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", str(SOLUTION / ".tools" / "browsers")
    )

from app import create_server
from playwright.sync_api import expect, sync_playwright


def run(record):
    artifacts = SOLUTION / ".artifacts" / ("browser-" + uuid4().hex[:8])
    artifacts.mkdir(parents=True)
    server = create_server(artifacts / "demo.sqlite3", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    captions = []
    duration = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                headless=True,
                slow_mo=230 if record else 0,
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                locale="ru-RU",
                timezone_id="Asia/Yekaterinburg",
                record_video_dir=str(artifacts) if record else None,
                record_video_size={"width": 1440, "height": 1000} if record else None,
            )
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            started = time.monotonic()

            def pause(caption, seconds=2):
                captions.append((time.monotonic() - started, caption))
                page.wait_for_timeout(seconds * 1000 if record else 120)

            def login(phone, register=False):
                if register:
                    page.get_by_role("button", name="Регистрация", exact=True).click()
                page.get_by_label("Номер телефона").fill(phone)
                page.get_by_role("button", name="Получить SMS-код").click()
                expect(page.get_by_label("Код из SMS")).to_be_visible()
                pause(
                    ("Регистрация заявителя" if register else "Вход по телефону")
                    + ". Учебный SMS-код: 123456.",
                    2,
                )
                page.get_by_label("Код из SMS").fill("123456")
                page.get_by_role(
                    "button",
                    name="Зарегистрироваться" if register else "Войти",
                    exact=True,
                ).click()
                expect(page.get_by_role("button", name="Выйти")).to_be_visible()

            def logout():
                page.get_by_role("button", name="Выйти", exact=True).click()
                expect(page.get_by_label("Номер телефона")).to_be_visible()

            def open_first():
                page.locator(".list-item h3 a").first.click()
                expect(
                    page.get_by_role("heading", name="Анкета и документы")
                ).to_be_visible()

            def assessment(correction):
                for code in ("5.1", "5.2", "5.3"):
                    page.locator(f'[name="assessment-{code}"]').select_option(
                        "NEEDS_CORRECTION"
                        if correction and code != "5.2"
                        else "CONFORMS"
                    )
                    comment = (
                        {
                            "5.1": "Приложите сертификат о знаниях.",
                            "5.2": "Цветная фотография представлена.",
                            "5.3": "Получите ИНН и укажите номер в анкете.",
                        }
                        if correction
                        else {
                            "5.1": "Сертификат приложен.",
                            "5.2": "Фото соответствует перечню.",
                            "5.3": "ИНН указан в анкете.",
                        }
                    )
                    page.locator(f'[name="comment-{code}"]').fill(comment[code])
                pause("Специалист оценивает каждый пункт: знания, фотографии и ИНН.", 3)
                page.get_by_role("button", name="Сохранить оценки", exact=True).click()
                expect(
                    page.get_by_role("heading", name="Сохранённые оценки")
                ).to_be_visible()

            # Genuine sample file bytes are uploaded through the visible forms.
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (300, 400), "#dbe9e3")
            draw = ImageDraw.Draw(image)
            draw.ellipse((105, 70, 195, 160), fill="#64857a")
            draw.rounded_rectangle((65, 175, 235, 340), radius=55, fill="#64857a")
            draw.text((105, 365), "DEMO 3x4", fill="#173d32")
            photo = artifacts / "photo.png"
            image.save(photo)
            # Minimal valid one-page PDF for demonstration, not a real certificate.
            stream = b"BT /F1 24 Tf 50 730 Td (DEMO - KNOWLEDGE CERTIFICATE) Tj 0 -45 Td /F1 13 Tf (Educational sample. Not an official document.) Tj ET"
            objects = [
                b"<< /Type /Catalog /Pages 2 0 R >>",
                b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"\nendstream",
            ]
            pdf = b"%PDF-1.4\n"
            offsets = [0]
            for i, obj in enumerate(objects, 1):
                offsets.append(len(pdf))
                pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
            xref = len(pdf)
            pdf += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
                f"{o:010d} 00000 n \n".encode() for o in offsets[1:]
            )
            pdf += (
                f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
            )
            certificate = artifacts / "certificate.pdf"
            certificate.write_bytes(pdf)

            page.goto(url)
            pause(
                "Лабораторная 6. Подготовка документов для патента. Роли: заявитель и специалист.",
                3,
            )
            page.screenshot(path=str(artifacts / "01-login.png"))
            login("+79991234567", register=True)
            pause(
                "Личный кабинет заявителя: заявки, завершённые услуги, индивидуальный перечень.",
                3,
            )
            page.screenshot(path=str(artifacts / "02-dashboard.png"))
            page.get_by_role("link", name="Начать подготовку").click()
            page.locator("#citizenship").select_option("Узбекистан")
            page.get_by_role("button", name="Показать индивидуальный перечень").click()
            expect(page.locator("#calculated")).to_contain_text("Получить ИНН")
            pause(
                "Анкета определяет три пункта: сертификат, цветные фото 3×4 и ИНН.", 3
            )
            page.locator("#file-52").set_input_files(photo)
            page.get_by_role(
                "button", name="Подать заявку", exact=True
            ).scroll_into_view_if_needed()
            pause(
                "Сначала отправляем только фотографию. Недостающие документы допускаются до проверки.",
                3,
            )
            page.get_by_role("button", name="Подать заявку", exact=True).click()
            expect(
                page.get_by_role("heading", name="Анкета и документы")
            ).to_be_visible()
            app_hash = page.url.split("#")[1]
            pause(
                "Заявка принята. Версия 1 и событие подачи сохранены в базе данных.", 3
            )
            logout()
            login("+79990000001")
            pause(
                "Специалист входит по заранее созданному номеру. Новая заявка находится в очереди.",
                3,
            )
            open_first()
            page.get_by_role("button", name="Начать проверку", exact=True).click()
            expect(
                page.get_by_role("heading", name="Оценка комплектности")
            ).to_be_visible()
            page.get_by_role(
                "heading", name="Оценка комплектности"
            ).scroll_into_view_if_needed()
            assessment(True)
            page.get_by_label("Инструкции или текст заключения").fill(
                "Приложите сертификат о знаниях. Получите ИНН и укажите его в анкете. Сохраните фотографию."
            )
            page.get_by_role(
                "button", name="Запросить дополнение", exact=True
            ).scroll_into_view_if_needed()
            pause(
                "Оценки сохранены без решения: статус остаётся «На проверке». Теперь запросим дополнение.",
                3,
            )
            page.get_by_role("button", name="Запросить дополнение", exact=True).click()
            expect(
                page.get_by_role("link", name="Дополнить заявку")
            ).not_to_be_visible()
            expect(page.locator(".callout")).to_contain_text("Требуется дополнение")
            pause("Запрос специалиста перевёл заявку в статус «Требует дополнения».", 3)
            logout()
            login("+79991234567")
            open_first()
            page.get_by_role("link", name="Дополнить заявку").click()
            expect(page.get_by_role("heading", name="Дополнить заявку")).to_be_visible()
            page.get_by_label("У меня уже есть ИНН").check()
            page.locator("#inn").fill("123456789012")
            page.get_by_label(
                "Есть документ, подтверждающий знание языка, истории и законодательства"
            ).check()
            page.locator("#file-51").set_input_files(certificate)
            expect(page.locator("[name=keep]")).to_be_checked()
            page.get_by_role(
                "button", name="Отправить дополнение", exact=True
            ).scroll_into_view_if_needed()
            pause(
                "Заявитель указывает ИНН, прикладывает сертификат и сохраняет фотографию первой версии.",
                4,
            )
            page.get_by_role("button", name="Отправить дополнение", exact=True).click()
            expect(
                page.get_by_role("heading", name="Анкета и документы")
            ).to_be_visible()
            expect(page.locator(".version-head")).to_contain_text("Версия 2")
            pause(
                "Дополнение принято как версия 2. Первая версия осталась неизменной.", 3
            )
            page.reload()
            expect(page.locator(".version-head")).to_contain_text("Версия 2")
            pause(
                "После перезагрузки сохранены анкета, документы, версии и история статусов.",
                3,
            )
            logout()
            login("+79990000001")
            open_first()
            page.get_by_role("button", name="Начать проверку", exact=True).click()
            expect(
                page.get_by_role("heading", name="Оценка комплектности")
            ).to_be_visible()
            assessment(False)
            page.get_by_label("Инструкции или текст заключения").fill(
                "Комплект документов по пунктам 5.1–5.3 соответствует индивидуальному перечню: сертификат и цветные фотографии приложены, ИНН указан. Подготовка комплекта завершена."
            )
            page.get_by_role(
                "button", name="Выдать заключение", exact=True
            ).scroll_into_view_if_needed()
            pause(
                "Повторная проверка: все три пункта соответствуют. Специалист выдаёт заключение.",
                3,
            )
            page.get_by_role("button", name="Выдать заключение", exact=True).click()
            expect(
                page.get_by_role("heading", name="Заключение о комплектности")
            ).to_be_visible()
            pause(
                "Заявка завершена. Результат — заключение о комплектности документов.",
                4,
            )
            logout()
            login("+79991234567")
            page.get_by_role("link", name="Оказанные услуги", exact=True).click()
            expect(page.locator(".list-item")).to_contain_text("Завершена")
            pause("Заявитель находит ранее оказанную услугу в архиве результатов.", 3)
            open_first()
            expect(
                page.get_by_role("heading", name="Заключение о комплектности")
            ).to_be_visible()
            expect(page.locator(".timeline li")).to_have_count(6)
            page.screenshot(path=str(artifacts / "03-result.png"), full_page=True)
            pause(
                "Получение результата: заключение и полная история шести переходов статуса.",
                4,
            )
            with page.expect_download() as download:
                page.get_by_role(
                    "link", name="certificate.pdf", exact=True
                ).first.click()
            destination = artifacts / "downloaded-certificate.pdf"
            download.value.save_as(destination)
            assert destination.read_bytes() == certificate.read_bytes()
            page.locator("summary").first.click()
            page.locator("summary").first.scroll_into_view_if_needed()
            pause(
                "Архивная версия 1: прежняя анкета, фотография, замечания и запрос специалиста.",
                4,
            )
            expect(page.locator("details[open]")).to_contain_text(
                "Нужно получить сертификат"
            )
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(url + "#" + app_hash)
            expect(
                page.get_by_role("heading", name="Заключение о комплектности")
            ).to_be_visible()
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            ), "Mobile overflow"
            page.screenshot(path=str(artifacts / "04-mobile.png"), full_page=True)
            pause(
                "Кабинет доступен и на узком экране. Все данные хранятся в SQLite.", 3
            )
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.evaluate("window.scrollTo(0,0)")
            pause(
                "Полный сценарий завершён: подача → проверка → дополнение → повторная проверка → заключение.",
                4,
            )
            assert not errors, errors
            duration = time.monotonic() - started
            video = page.video
            context.close()
            video_path = video.path() if record else None
            browser.close()
        if record:
            from imageio_ffmpeg import get_ffmpeg_exe

            report = SOLUTION.parent / "report"
            report.mkdir(exist_ok=True)
            subtitles = artifacts / "captions.srt"

            def stamp(seconds):
                ms = int(seconds * 1000)
                return (
                    f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
                )

            with subtitles.open("w", encoding="utf-8") as output:
                for i, (start, caption) in enumerate(captions):
                    end = captions[i + 1][0] if i + 1 < len(captions) else duration
                    output.write(
                        f"{i+1}\n{stamp(start)} --> {stamp(end)}\n{caption}\n\n"
                    )
            subprocess.run(
                [
                    get_ffmpeg_exe(),
                    "-y",
                    "-i",
                    str(video_path),
                    "-vf",
                    "pad=iw:ih+120:0:0:color=0x142e3c,subtitles=captions.srt:force_style='FontName=Arial,FontSize=9,Outline=1,MarginV=6'",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(report / "lab06-demo.mp4"),
                ],
                cwd=artifacts,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=(artifacts / "encoding.log").open("w"),
            )
            (report / "lab06-demo.srt").write_bytes(subtitles.read_bytes())
        print(
            f"Browser workflow OK. Artifacts: {artifacts}. Duration: {duration:.1f}s",
            flush=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    run(parser.parse_args().record)
