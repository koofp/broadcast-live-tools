import asyncio

async def main():
    import aiohttp
    from blrec.bili.api import WebApi
    from blrec.bili.wbi import make_key

    session = aiohttp.ClientSession()
    api = WebApi(
        session,
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'},
        room_id=14323359,
    )
    api.base_api_urls = ['https://api.bilibili.com']
    api.base_live_api_urls = ['https://api.live.bilibili.com']

    print('=== 1) nav 拉取 WBI 密钥 ===')
    try:
        nav = await api.get_nav()
        wbi = nav.get('wbi_img', {})
        img = wbi.get('img_url', '')
        sub = wbi.get('sub_url', '')
        img_key = img.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        sub_key = sub.rsplit('/', 1)[-1].rsplit('.', 1)[0]
        print(f'nav OK: isLogin={nav.get("isLogin")} img_key={img_key} sub_key={sub_key}')
        api.__class__._wbi_key = make_key(img_key, sub_key)
        api.__class__._wbi_key_mtime = time.monotonic()
        print(f'新 WBI key: {api.__class__._wbi_key}')
    except Exception as e:
        print('nav FAIL:', repr(e)[:250])
        await session.close()
        return

    print('=== 2) 用新密钥请求弹幕信息 ===')
    try:
        info = await api.get_danmu_info(14323359)
        print('弹幕信息 OK! host_list 数:', len(info.get('host_list', [])))
    except Exception as e:
        print('弹幕信息 FAIL:', repr(e)[:300])

    print('=== 3) 对照：旧硬编码密钥 ===')
    api.__class__._wbi_key = make_key(
        '7cd084941338484aae1ad9425b84077c', '4932caff0ff746eab6f01bf08b70ac45')
    try:
        info = await api.get_danmu_info(14323359)
        print('旧密钥也 OK?!')
    except Exception as e:
        print('旧密钥 FAIL:', repr(e)[:200])

    await session.close()

asyncio.run(main())
