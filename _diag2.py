import asyncio

async def main():
    from blrec.setting.models import Settings
    import tomli as tomllib

    tasks_block = """
[[tasks]]
room_id = 14323359
enable_monitor = true
enable_recorder = true

[[tasks]]
room_id = 71003
enable_monitor = true
enable_recorder = true
"""
    base = open('/app/settings.toml', 'rb').read().decode('utf-8-sig')
    d = tomllib.loads(base + tasks_block)
    print('tasks raw:', [(t['room_id'], t.get('enable_monitor'), t.get('enable_recorder')) for t in d.get('tasks', [])])
    try:
        s = Settings.model_validate(d) if hasattr(Settings, 'model_validate') else Settings(**d)
        print('PARSE OK, tasks:', [(t.room_id, t.enable_monitor, t.enable_recorder) for t in s.tasks])
    except Exception as e:
        print('PARSE FAIL:', type(e).__name__)
        print(str(e)[:1000])

asyncio.run(main())
