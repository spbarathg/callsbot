import asyncio
import pytest
from bot.vip import count_vip_holders_for_token


@pytest.mark.asyncio
async def test_count_vip_holders(monkeypatch):
    async def fake_rpc(method: str, params: list):
        if method == 'getTokenAccountsByOwner':
            return {"value": [{"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 10}}}}}}]}
        return {"value": []}

    import bot.vip as v
    v.solana_rpc = fake_rpc
    holders = await count_vip_holders_for_token("mint", ["wallet1", "wallet2"])
    assert "wallet1" in holders or "wallet2" in holders


