from research.round9_data import fetch_okx_1m_marks
okx = fetch_okx_1m_marks("BTC-USDT-SWAP", pages=6000)
print(f"fetched {len(okx)} 1m marks")
print(f"span: {(okx[-1]['ts']-okx[0]['ts'])/86400000:.1f} days")
