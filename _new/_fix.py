from pathlib import Path
import ast

p = Path("core/screen_share_manager.py")
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
start = next(i for i, l in enumerate(lines)
             if l.startswith("    # ── Одна дверь")
             or l.startswith("    # ── Quota guard"))
end = len(lines)
for i in range(start + 1, len(lines)):
    if lines[i].strip() and not lines[i].startswith((" ", "\t")):
        end = i
        break

NL = chr(92) + "n"          # два знака: обратная косая и n, внутри f-строки
body = [
    '    # ── Одна дверь к модели: core/aux_model.aux_call ─────────────────\n',
    '    # Здесь лежала КОПИЯ этой двери: свой клиент SDK, своя проверка\n',
    '    # остывания, свой разбор 429 — шестьдесят строк, повторяющих то, что\n',
    '    # общая дверь уже умеет, включая картинки и порядок «картинка → текст».\n',
    '    #\n',
    '    # Удалена в блоке 5, а не оставлена под флагом (правило 9): пока копия\n',
    '    # жива, вызов через неё не попадает в учёт расхода, и инвариант I16 «ни\n',
    '    # один вызов мимо метеринга» — ложь. Учитывать копию было бы дешевле,\n',
    '    # чем удалить, и это ровно та экономия, из-за которой через месяц никто\n',
    '    # не знает, какая из двух дверей работает.\n',
    '    prompt = (\n',
    f'        f"You are analyzing a screen capture from the user\'s computer.{NL}"\n',
    f'        f"Capture source: {{frame.source_desc}}{NL}"\n',
    f'        f"Frame captured {{round(frame.age_seconds(), 1)}}s ago.{NL}{NL}"\n',
    f'        f"User question: {{question}}{NL}{NL}"\n',
    '        "Analyze what is visible on the screen and answer the user\'s question "\n',
    '        "directly, concisely, and helpfully. If you can see text, UI elements, "\n',
    '        "or other context, describe what you see and give actionable guidance. "\n',
    '        "Be concise and actionable."\n',
    '    )\n',
    '    from core.aux_model import aux_call\n',
    '    ok, answer = aux_call(prompt, api_key,\n',
    '                          image_parts=[(frame.data, frame.mime_type)],\n',
    '                          caller="ScreenShare", role="vision")\n',
    '    if ok:\n',
    '        return answer\n',
    '    # Отказ уже назван вслух внутри aux_call; здесь — чем ответим владельцу.\n',
    '    if answer.startswith("[quota-cap"):\n',
    '        return ("Screen View is capturing normally, but today\'s own limit for "\n',
    '                "vision calls is used up. The screen is still being watched.")\n',
    '    if answer.startswith(("[quota-cooldown", "[quota-429")):\n',
    '        return ("Screen View is ON and capturing frames normally, but the "\n',
    '                "vision model is cooling down after a rate limit. Your screen "\n',
    '                "is still being captured — ask again shortly.")\n',
    '    return f"Screen analysis failed: {answer}"\n',
]
p.write_text("".join(lines[:start]) + "".join(body) + "".join(lines[end:]),
             encoding="utf-8", newline="\n")
src = p.read_text(encoding="utf-8")
ast.parse(src)
print("udaleno strok:", end - start, "| sintaksis: ok")
print("genai ostalsya:", "genai" in src, "| generate_content:", "generate_content" in src)
print("zovet obshchuyu dver:", "aux_call" in src)
