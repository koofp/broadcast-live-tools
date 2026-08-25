import asyncio

async def main():
    from blrec.setting.models import Settings
    try:
        import tomli as tomllib
    except ImportError:
        import toml
        class _T:
            @staticmethod
            def loads(s):
                return toml.loads(s)
        tomllib = _T()
    d = tomllib.loads(open('/app/settings.toml', 'rb').read().decode('utf-8-sig'))
    print('toml tasks raw:', d.get('tasks'))
    try:
        s = Settings.model_validate(d) if hasattr(Settings, 'model_validate') else Settings(**d)
        print('PARSE OK, tasks:', [(t.room_id, t.enable_monitor, t.enable_recorder) for t in s.tasks])
    except Exception as e:
        print('PARSE FAIL:', type(e).__name__)
        print(str(e)[:800])

asyncio.run(main())
