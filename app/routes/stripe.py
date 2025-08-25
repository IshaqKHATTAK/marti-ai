import logging
from fastapi import BackgroundTasks, Request, HTTPException, APIRouter, Depends
from fastapi.responses import JSONResponse
import stripe.webhook
from app.models.user import User
import stripe
from sqlalchemy.orm import Session
from app.services.payment import update_user_payment
from app.common.env_config import get_envs_setting
from app.services.auth import get_current_user
from app.models.user import User
from app.common.database_config import get_async_db
from app.services.payment import adjust_tier_degration_change, update_payment_details, handle_invoice_update

logger = logging.getLogger(__name__)

settings = get_envs_setting()

# This is your test secret API key.
stripe.api_key = settings.STRIPE_SECRET_KEY

stripe_router = APIRouter(
    prefix="/stripe",
    tags=["Stripe"],
    responses={404: {"description": "Not found, something wrong with auth"}},
    dependencies=[]
)



# @stripe_router.post("/webhook")
# async def webhook_received(request: Request, background_tasks: BackgroundTasks, session: Session =  Depends(get_async_db)):
#     try:
#         # Correctly retrieve the raw body for signature verification
#         # payload = await request.body()
#         signature = request.headers.get('stripe-signature')
#         if not signature:
#             logger.error("Stripe signature missing in webhook request.")
#             raise HTTPException(status_code=400, detail="Missing Stripe signature.")

#         print(f"Headers: {request.headers}")
#         print(f'signature == {signature}')
#         request_body_bytes = await request.body()  # This reads the body as bytes
#         event = stripe.Webhook.construct_event(
#             payload=request_body_bytes,  # Pass the raw bytes of the request body
#             sig_header=signature,
#             secret=settings.STRIPE_WEBHOOK_SECRET)
        
#         data = event['data']
#     except Exception as e:
#         logger.exception(e)
#         raise HTTPException(status_code=400, detail="Webhook signature verification failed.")
    
#     event_type = event['type']
#     data_object = data['object']

#     # Handle the event accordingly
#     if event_type == 'customer.subscription.created':
#         print('created called from route')
#         if data_object["customer"]:
#             subscription_id = data_object["id"]
#             customer_id = data_object["customer"]
#             print(f'subscription id == {subscription_id} == {customer_id}')
#             print(f"📌 Event Data: {data_object}")
#             user_data = await update_user_payment(customer_id, subscription_id, session)
#             # await adjust_tier_degration_change(session, user_data)
#     if event_type == 'customer.subscription.updated':
#         print(f'updated called from route')
#         if data_object["customer"]:
#             subscription_id = data_object["id"]
#             customer_id = data_object["customer"]
#             print(f'subscription id == {subscription_id} == {customer_id}')
#             print(f"📌 Event Data: {data_object}")
#             user_data = await update_user_payment(customer_id, subscription_id, session)
#             await adjust_tier_degration_change(session, user_data)
#     if event_type == 'customer.subscription.deleted':
#         if data_object["status"] == "canceled":
#             customer_id = data_object["customer"]
#             subscription_id = data_object["id"]
#             print(f'inside delete')
#             user_data = await update_user_payment(customer_id, subscription_id, session)
#             await adjust_tier_degration_change(session, user_data)
#             # Subscription Cancellation Email
#             # user = session.query(User).filter(User.stripeId == customer_id).first()
#             # await send_subscription_cancellation_email(user, background_tasks)

#     # Handle other events...
#     print(f'done with webhook')
#     return JSONResponse({'status': 'success'})

 

@stripe_router.post("/webhook")
async def webhook_received(
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_async_db)
):
    try:
        signature = request.headers.get('stripe-signature')
        request_body_bytes = await request.body()
        event = stripe.Webhook.construct_event(
            payload=request_body_bytes,
            sig_header=signature,
            secret=settings.STRIPE_WEBHOOK_SECRET
        )
        data = event['data']
    except Exception as e:
        logger.exception("Webhook signature verification failed.")
        raise HTTPException(status_code=400, detail="Webhook signature verification failed.")

    event_type = event['type']
    data_object = data['object']

    if event_type in ['customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted']:
        # await handle_subscription_update(data_object, session)

        if data_object.get("customer"):
            subscription_id = data_object["id"]
            customer_id = data_object["customer"]

            if event_type in ['customer.subscription.created', 'customer.subscription.updated']:
                if data_object["status"] == "active":
                    print(f'active webhook called')
                    user_data = await update_payment_details(customer_id, subscription_id, session)
                    await adjust_tier_degration_change(session, user_data)

            elif event_type == 'customer.subscription.deleted':
                if data_object["status"] == "canceled":
                    print('cancel subscription')
                    user_data = await update_payment_details(customer_id, subscription_id, session)
                    await adjust_tier_degration_change(session, user_data)

    elif event_type in ['invoice.payment_succeeded', 'invoice.payment_failed']:
        await handle_invoice_update(data_object, session)

    return JSONResponse({'status': 'success'})

