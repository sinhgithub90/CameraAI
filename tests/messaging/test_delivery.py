from unittest.mock import AsyncMock

import pytest

from camera_ai.messaging.contracts import Delivery, DeliveryAlreadyFinalized


@pytest.mark.asyncio
async def test_delivery_can_be_finalized_exactly_once():
    ack, retry, reject = AsyncMock(), AsyncMock(), AsyncMock()
    delivery = Delivery(
        task="task-1", attempt=0,
        ack_callback=ack, retry_callback=retry, reject_callback=reject,
    )
    await delivery.ack()
    ack.assert_awaited_once()
    with pytest.raises(DeliveryAlreadyFinalized):
        await delivery.retry()
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_and_reject_use_their_own_callback():
    callbacks = [AsyncMock(), AsyncMock(), AsyncMock()]
    retry_delivery = Delivery(
        task="retry", attempt=2,
        ack_callback=callbacks[0], retry_callback=callbacks[1], reject_callback=callbacks[2],
    )
    await retry_delivery.retry()
    callbacks[1].assert_awaited_once()

    reject = AsyncMock()
    reject_delivery = Delivery(
        task="reject", attempt=0,
        ack_callback=AsyncMock(), retry_callback=AsyncMock(), reject_callback=reject,
    )
    await reject_delivery.reject()
    reject.assert_awaited_once()
