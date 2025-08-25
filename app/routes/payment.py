from stripe import StripeError, stripe
import logging
from fastapi import HTTPException, APIRouter, Depends
from app.common.env_config import get_envs_setting
from app.services.auth import get_current_user
from app.models.user import User
from app.common.database_config import get_async_db
from sqlalchemy.orm import Session
from app.services import payment
from fastapi import BackgroundTasks
from app.models.user import Plan
from app.services.payment import adjust_tier_degration_change
from app.schemas.request.payment import SubscriptionRequest, UpdateSubscriptionRequest, ValidatePromoCodeRequest, UpdateCardRequest
from app.utils.db_helpers import insert_logs
logger = logging.getLogger(__name__)
envs = get_envs_setting()

# This is your test secret API key.
stripe.api_key = envs.STRIPE_SECRET_KEY

payments_router_protected = APIRouter(
    prefix="/api/v1/payment",
    tags=["Payment"],
    responses={404: {"description": "Not found, something wrong with auth"}},
    # dependencies=[Depends(oauth2_scheme), Depends(validate_access_token),]
)

from sqlalchemy.ext.asyncio import AsyncSession
 
def get_price_amount(price):
    """Extract price amount based on pricing model"""
    if price.billing_scheme == 'tiered':
        # For tiered pricing, get the first tier's flat_amount or unit_amount
        if hasattr(price, 'tiers') and price.tiers and len(price.tiers) > 0:
            first_tier = price.tiers[0]
            
            #Use flat_amount if available, otherwise unit_amount
            if first_tier.get('flat_amount') is not None:
                return first_tier['flat_amount']
            elif first_tier.get('unit_amount') is not None:
                return first_tier['unit_amount']
        # If no tiers data, return 0 for display purposes
        return 0
    elif price.billing_scheme == 'per_unit':
        # Standard per-unit pricing
        return price.unit_amount or 0
    else:
        return 0
            

@payments_router_protected.get("/plans")
async def get_prices(session: Session = Depends(get_async_db), current_user: User = Depends(get_current_user)):
    try:
        prices = await stripe.Price.list_async(active=True, expand=['data.product', 'data.tiers'])
        filtered_plans = {"month": [], "year": []}
        add_on_price_ids = {"month": [], "year": []}
        for price in reversed(prices.data):
            product = price.product
            metadata = product.get("metadata", {})
            is_addon = product.get("metadata", {}).get("type") == "addon"
            if product.get("metadata", {}).get("type") == "metered_user":
                print(f'price data fo a plan == {price}')
            interval = price.recurring.get("interval") if price.recurring else None
            print(f'interval = {interval}')
 
            # skip one-time or unsupported pricing
            if interval not in ["month", "year"]:
                continue
            price_amount = get_price_amount(price)
            plan_data = {
                "name": product.name,
                "price_id": price.id,
                "active": price.active,
                "currency": price.currency,
                "description": product.get("description", "") if product else "",
                "metadata": product.get("metadata", {}) if product else {},
                "price": price.unit_amount // 100 if product.get("metadata", {}).get("type") != "metered_user" else price_amount//100,
                "total_price": price.unit_amount // 100 if product.get("metadata", {}).get("type") != "metered_user" else price_amount//100,
                "is_enabled": False if product.name == "Enterprise" else True,
                "sort_number": int(metadata.get("number", 9999))
            }
            if interval == "year":
                plan_data["price"] = plan_data["price"] // 12
            if is_addon:
                # add_on_price_ids.append(plan_data)
                add_on_price_ids[interval].append(plan_data)
            else:
                # filtered_plans.append(plan_data)
                filtered_plans[interval].append(plan_data)
        for interval in ["month", "year"]:
            filtered_plans[interval].sort(key=lambda x: x["sort_number"])
            add_on_price_ids[interval].sort(key=lambda x: x["sort_number"])
        # Get user's subscription cancellation details
        is_cancelled = False
        cancel_at_formatted = None

        if current_user.stripeId:
            subs = await stripe.Subscription.list_async(
                customer=current_user.stripeId,
                status="active",
                limit=1
            )
            is_cancelled = False
            cancel_at_formatted = None
            renews_at_formatted = None
            previous_addon_plan_id = None
            previous_base_plan_id = None
            is_yearly = False
            is_monthly = False
            # if not subs:
            #     subs = await stripe.Subscription.list_async(
            #         customer=current_user.stripeId,
            #         status="canceled",
            #         limit=1,
            #         expand=["data.items"]
            #     )
            #     if subs.data:
            #         print(f'subs.data = {subs.data}')
            #         canceled_sub = subs.data[0]
            #         for item in canceled_sub["items"]["data"]:
            #             price = item["price"]
            #             base_plan = is_base_plan(price["id"])
            #             if base_plan:  
            #                 previous_base_plan_id = price["id"]
            #             else:
            #                 previous_addon_plan_id = price["id"]


            if subs.data:
                sub = subs.data[0]
                is_cancelled = sub.cancel_at_period_end
                cancel_at = sub.get("cancel_at")
                current_period_end = sub.get("current_period_end")

                for item in sub["items"]["data"]:
                    price = item["price"]
                    if is_base_plan(price["id"]):
                        recurring = price.get("recurring")
                        if recurring:
                            interval = recurring.get("interval")
                            if interval == "year":
                                is_yearly = True
                            elif interval == "month":
                                is_monthly = True
                if cancel_at:
                    cancel_at_formatted = datetime.utcfromtimestamp(cancel_at).strftime("%Y-%m-%d %H:%M:%S UTC")
                if current_period_end:
                    renews_at_formatted = datetime.utcfromtimestamp(current_period_end).strftime("%Y-%m-%d %H:%M:%S UTC")
            else:
                # No ACTIVE subscription — check most recent canceled subscription for previous plan info
                subs = await stripe.Subscription.list_async(
                    customer=current_user.stripeId,
                    status="canceled",
                    limit=1,
                    expand=["data.items"]
                )
                if subs.data:
                    canceled_sub = subs.data[0]
                    for item in canceled_sub["items"]["data"]:
                        price = item["price"]
                        base_plan = is_base_plan(price["id"])
                        if base_plan:  
                            previous_base_plan_id = price["id"]
                        else:
                            previous_addon_plan_id = price["id"]
        from app.services.payment import get_current_user_seats
        total_users_paid = await get_current_user_seats(stripe_customer_id = current_user.stripeId)
        user_plans = [
            {
                "current_plan": current_user.current_plan.value,
                "current_users":total_users_paid,
                "is_cancelled": is_cancelled,
                "cancel_at": cancel_at_formatted,
                "renews_at":renews_at_formatted,
                "previous_addon_id":[previous_addon_plan_id],
                "previous_plan_id":previous_base_plan_id,
                "is_yearly": is_yearly,
                "is_monthly": is_monthly
            }
        ]

        return {
            "plans": filtered_plans,
            "user_plans": user_plans,
            "addons": add_on_price_ids
        }
    except StripeError as e:
        logger.exception(e)
        raise HTTPException(status_code=400, detail=str(e))

@payments_router_protected.post("/create-subscription")
async def create_subscription(
    request: SubscriptionRequest,
    db: AsyncSession = Depends(get_async_db), 
    user: User = Depends(get_current_user),
    ):
    # try:
        if request.priceId in [envs.STRIPE_ENTERPRISE_PRICE_ID_YEARLY, envs.STRIPE_ENTERPRISE_PRICE_ID_MONTHLY] and request.no_of_users_apart_from_admin < 100:
            raise HTTPException(status_code=400, detail="You must have at least 100 users to subscribe to enterprise plan.")

        if not user.stripeId:
            raise HTTPException(status_code=400, detail="Stripe ID not found")
        promo_codes = await stripe.PromotionCode.list_async(
            code=request.coupon,
            active=True,
            limit=1,
        )
        if not promo_codes.data or not promo_codes.data[0].coupon:
            raise HTTPException(status_code=404, detail="Promo code is invalid.")
        invoices = await stripe.Invoice.list_async(
                customer=user.stripeId,
                limit=100  # adjust as needed
            )
        
        #  Check if user already has a base plan (not an addon)
        existing_subs = await stripe.Subscription.list_async(
            customer=user.stripeId,
            status="active",
            expand=["data.items.data.price"],
            limit=10
        )
        for invoice in invoices.data:
            discount = invoice.get("discount")
            if discount:
                invoice_coupon = discount.get("coupon")
                if invoice_coupon and invoice_coupon["id"] == promo_codes.data[0].coupon.id:
                    raise HTTPException(
                        status_code=400,
                        detail="This coupon has already been used by the customer."
                    )
        # Step 2: Check for non-addon base plans
        for sub in existing_subs.data:
            for item in sub["items"]["data"]:
                price = item["price"]
                product_id = price["product"]
                
                # Manual fetch for product to access metadata
                product = await stripe.Product.retrieve_async(product_id)
                metadata = product.get("metadata", {})
                is_addon = metadata.get("type") == "addon"

                if not is_addon:
                    raise HTTPException(
                        status_code=400,
                        detail="You already have an active base subscription."
                    )
        # Base subscription item
        items = [{"price": request.priceId}]
        if request.no_of_users_apart_from_admin:
            items.append({"price":request.user_usage_priceId,"quantity":request.no_of_users_apart_from_admin})
        # coupon_item = []
        # If image_generation is True, add the addon price
        if request.add_on.get('image_generation'):
            items.append({"price": envs.STRIPE_IMAGE_GENERATION_PRICE_ID})  # addon
        # If coupon is provided, add the coupon item
        # if request.coupon and promo_codes.data and promo_codes.data[0].coupon:
        #     coupon_item.append()
            
        # Create subscription
        subscription = await stripe.Subscription.create_async(
            customer=user.stripeId,
            items=items,
            discounts=[{'coupon': promo_codes.data[0].coupon}] if request.coupon else None,
            payment_behavior="default_incomplete",
            expand=["latest_invoice.payment_intent"],
        )
        print(f'subscription id = {subscription}')
        
        # Get the latest invoice ID
        latest_invoice = subscription.get("latest_invoice", {})
        print(f'latest invoice == {latest_invoice}')
        if not latest_invoice:
            raise HTTPException(status_code=400, detail="Latest invoice not found")
        
        print(f'invoice  == {latest_invoice}')
        payment_intent = latest_invoice.get("payment_intent")
        print(f'payment intent == {payment_intent}')
        #This part need to be correctly handle PLEASE KEEP IN MIND!!!!!!!!!!!!!
        # if not payment_intent:
        #     raise HTTPException(status_code=400, detail="Payment intent not found")

        if payment_intent is None:
            # No payment required (100% discount, free trial, or $0 amount)
            
            setup_intent = await stripe.SetupIntent.create_async(
                customer=user.stripeId,
                payment_method_types=["card"]
                )
            
            # payment_method = stripe.PaymentMethod.create(  
            #     customer=user.stripeId,
            #     type="card",
            # )
            # Log the successful subscription
            await insert_logs(
                user.organization_id, 
                f"organization has been successfully subscribed to plan (no payment required).", 
                f'{user.name}', 
                "Subscription", 
                db
            )
            
            
            return {
                "clientSecret": setup_intent.client_secret,  # Setup Intent client secret
                "setup_intent": setup_intent,
                # "payment_method": None,
                # "subscription": subscription,
                "status": "requires_payment",  # Different status
                "message": "Please add a payment method for future billing",
                
            }
        else:
            # Payment is required
            client_secret = payment_intent.get("client_secret")
            print(f'client secrets = {client_secret}')
            
            if not client_secret:
                raise HTTPException(status_code=400, detail="Client secret not found in payment intent")
            
            # Log the subscription creation (payment still pending)
            await insert_logs(
                user.organization_id, 
                f"organization subscription created - payment pending.", 
                f'{user.name}', 
                "Subscription", 
                db
            )
            
            return {
                "clientSecret": client_secret,
                "subscription": subscription,
                "status": "requires_payment",
                "message": "Please complete payment to activate subscription"
            }
        # client_secret = payment_intent.get("client_secret")
        # print(f'client secrets = {client_secret}')
        # # background_tasks.add_task(insert_logs, user.organization_id, f"{user.organization.name} has been successfully subscribed to plan.", f'{user.name}', "Subscription", db)

        # await insert_logs(user.organization_id, f"organization has been successfully subscribed to plan.", f'{user.name}', "Subscription", db)
    
        # return {
        #     "clientSecret": client_secret,
        #     "subscription": subscription
        # }
    # except Exception as e:
    #     raise HTTPException(status_code=400, detail=str(e))

@payments_router_protected.post("/validate-promo-code")
async def validate_promo_code(
    request: ValidatePromoCodeRequest,
    user: User = Depends(get_current_user)
):
    try:
        # Fetch the promo code from Stripe
        promo_codes = await stripe.PromotionCode.list_async(
            code=request.code,
            active=True,
            limit=1,
        )

        if not promo_codes.data:
            raise HTTPException(status_code=404, detail="Promo code not found or inactive")
        invoices = await stripe.Invoice.list_async(
                customer=user.stripeId,
                limit=100  # adjust as needed
            )
        
        # Extract the coupon ID you're about to apply
        new_coupon_id = promo_codes.data[0].coupon.id 
        for invoice in invoices.data:
            discount = invoice.get("discount")
            if discount:
                invoice_coupon = discount.get("coupon")
                if invoice_coupon and invoice_coupon["id"] == new_coupon_id:
                    raise HTTPException(
                        status_code=400,
                        detail="This coupon has already been used by the customer."
                    )
        promo_code = promo_codes.data[0]
        print(f'promo_code == {promo_code}')
        # Check for expiration or usage limits
        coupon = promo_code["coupon"]

        # Optionally check 
        
        return {
            "valid": True,
            "discount_type": "percentage" if coupon["percent_off"] else "amount",
            "discount_value": coupon.get("percent_off") or coupon.get("amount_off"),
            "promo_code_id": promo_code["id"],
            "message": "Promo code is valid"
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e.user_message or str(e)))


@payments_router_protected.post("/update-subscription")
async def update_subscription(request: UpdateSubscriptionRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    # try:
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="Stripe ID not found")
        if request.priceId == envs.STRIPE_ENTERPRISE_PRICE_ID:
            raise HTTPException(status_code=400, detail="Unauthautherize operation")
        # Enforce base plan presence
        if not request.priceId:
            raise HTTPException(status_code=400, detail="A base plan is required.")
        promo_codes = None
        if request.coupon:
            promo_codes = await stripe.PromotionCode.list_async(
                code=request.coupon,
                active=True,
                limit=1,
            )
            if not promo_codes.data or not promo_codes.data[0].coupon:
                raise HTTPException(status_code=404, detail="Promo code is invalid.")
            
        # Check for default payment method - only expand invoice_settings.default_payment_method
        customer = stripe.Customer.retrieve(
            user.stripeId, 
            expand=["invoice_settings.default_payment_method"]
        )
        print(f'after first susbscrption')
        default_payment_method = None
        if customer.get("invoice_settings", {}).get("default_payment_method"):
            default_payment_method = customer.get("invoice_settings", {}).get("default_payment_method", {}).get("id")
        if not customer.get("invoice_settings", {}).get("default_payment_method"):
            # Get all payment methods for the customer
            payment_methods = stripe.PaymentMethod.list(
                customer=user.stripeId,
                type="card"
            )
            if payment_methods and payment_methods.data:
                stripe.Customer.modify(
                    user.stripeId,
                    invoice_settings={"default_payment_method": payment_methods.data[0].id}
                )
                default_payment_method = payment_methods.data[0].id
                print(f"Set default payment method to {payment_methods.data[0].id}")
            else:
                raise HTTPException(
                    status_code=400,
                    detail="You must add a payment method before updating your plan."
                )
            
        # Fetch the current subscription
        current_subscription = stripe.Subscription.list(
            customer=user.stripeId,
            # status="active",
            limit=1,
            expand=["data.items"]
        )
        print(f'another stipe.')
        #stop if the user changing plan from yearly to monthly
        current_base_plan_item = next(
            (item for item in current_subscription["data"][0]['items']['data'] 
                if is_base_plan(item.price.id)), 
            None
        )
        if current_base_plan_item and current_base_plan_item.price.recurring.get("interval") == "year":
            # Get the new plan's interval
            new_plan_price = await stripe.Price.retrieve_async(request.priceId)
            if new_plan_price.recurring and new_plan_price.recurring.get("interval") == "month":
                raise HTTPException(
                    status_code=400, 
                    detail="Cannot downgrade from yearly to monthly plan. Please cancel your current subscription and wait for it to expire before subscribing to a monthly plan."
                )
            # raise HTTPException(
            #     status_code=400, 
            #     detail="Cannot downgrade from yearly to monthly plan. Please cancel your current subscription and wait for it to expire before subscribing to a monthly plan."
            # )
        # If still empty, raise error
        if not current_subscription:
            raise HTTPException(status_code=404, detail="No valid subscription found to update.")
        subscription = current_subscription["data"][0] 
        subscription_data = current_subscription.data[0]
        subscription_items = subscription["items"]["data"]
        
        #if the current subscription is not active e.g. cancel status, due status etc.
        if subscription_data.status != "active":
            previous_base_price = None
            user.current_plan
            if user.current_plan == Plan.starter:
                previous_base_price = envs.STRIPE_STARTER_PRICE_ID 
            elif user.current_plan == Plan.enterprise:
                previous_base_price = envs.STRIPE_ENTERPRISE_PRICE_ID 
            if not previous_base_price:
                raise HTTPException(status_code=400, detail="Please create your plan first then you can update")
            
            expected_price_ids = [previous_base_price]
            if user.add_on_features and "image_generation" in user.add_on_features:
                expected_price_ids.append(envs.STRIPE_IMAGE_GENERATION_PRICE_ID)
            print(f'[DB] Expected price IDs: {expected_price_ids}')

            original_items = []
            existing_price_to_id = {
                item["price"]["id"]: item["id"] for item in subscription_items
            }
            for price_id in expected_price_ids:
                if price_id in existing_price_to_id:
                    # Reuse existing item ID
                    original_items.append({
                        "id": existing_price_to_id[price_id],
                        "price": price_id
                    })
                else:
                    # Add as a new item
                    original_items.append({
                        "price": price_id
                    })
            print(f'deduplicated_items = {original_items}')
            for item in subscription_items:
                price_id = item["price"]["id"]
                item_id = item["id"]
                if price_id not in expected_price_ids:
                    stripe.SubscriptionItem.delete(item_id)
                    print(f" Deleted item: {item_id} (price: {price_id})")

            selected_price_ids = {item.get('price') for item in original_items if 'price' in item}
            if (
                envs.STRIPE_STARTER_PRICE_ID in selected_price_ids and
                envs.STRIPE_IMAGE_GENERATION_PRICE_ID in selected_price_ids
            ):
                raise HTTPException(status_code=404, detail="You are not allowed to subscribe to image generation addon with starter plan.")
            seen = set()
            deduplicated_items = []
            for item in original_items:
                key = item.get("id") or item.get("price")
                if key and key not in seen:
                    deduplicated_items.append(item)
                    seen.add(key)
            original_items = deduplicated_items
            stripe.Subscription.modify(
                subscription_data.id,
                items=original_items,
                proration_behavior="none",
                discounts=[{"coupon": promo_codes.data[0].coupon}] if request.coupon else None,
                #coupon=request.coupon if request.coupon else None,
                expand=["latest_invoice"]
            )
            
            subscription_data = stripe.Subscription.retrieve(
                subscription_data.id,
                expand=["items"]
            )
            print(f'update from db hdasere  = {subscription_data}')
        
        print(f'little after')
        subscription_id = subscription_data.id     
        if subscription_data.get("cancel_at_period_end"):
            stripe.Subscription.modify(
                subscription_id,
                cancel_at_period_end=False
            )
        print(f'low')
        current_items = subscription_data['items']['data']
        print(f' Current items on subscription:')
        for item in current_items:
            print(f" Item ID: {item['id']}, Price ID: {item['price']['id']}")

        updated_items = []

        # Base plan (assume always one base plan)
        base_item = current_items[0]
        updated_items.append({
            "id": base_item.id,
            "price": request.priceId
        })
        print(f'before just above')
        if request.no_of_users_apart_from_admin is not None and request.user_usage_priceId:
            # 1. delete any existing user-item
            for item in subscription["items"]["data"]:
                print(f'items price ids == {item.price.id}')
                if not is_base_plan(item.price.id):
                        print(f'usage userage plan deltion occure')
                        updated_items.append({"id": item.id, "deleted": True})
                        
            
            updated_items.append({
                "price": request.user_usage_priceId,
                "quantity": max(0, request.no_of_users_apart_from_admin)
            })

            
        print(f'base_item zero == {base_item.id} -- {request.priceId}')
        # Add-on handling
        addon_item = next(
                (item for item in current_items if item['price']['id'] == envs.STRIPE_IMAGE_GENERATION_PRICE_ID),
                None
            )
        if request.add_on.get('image_generation'):
            if addon_item:
                # If already present, keep it as-is (or optionally update quantity here)
                updated_items.append({
                    "id": addon_item["id"],
                    "price": envs.STRIPE_IMAGE_GENERATION_PRICE_ID
                })
            else:
                # If not present, add it
                updated_items.append({
                    "price": envs.STRIPE_IMAGE_GENERATION_PRICE_ID
                })
        elif addon_item:
            # If disabling and it exists, remove it
            updated_items.append({
                "id": addon_item["id"],
                "deleted": True
            })
        print(f'addons item = {addon_item}')
        for item in updated_items:
            print(f"updated Items: {item}")

        print(f'third')
        #  Modify subscription with proration behavior
        # unique_prices = set()
        # 
        # for item in updated_items:
        #     if item.get("price"):
        #         price = item.get("price")
        #         if price not in unique_prices:
        #             deduplicated_items.append(item)
        #             unique_prices.add(price)
        #     else:
        #         deduplicated_items.append(item)
        
        seen = set()
        deduplicated_items = []
        for item in updated_items:
            key = item.get("id") or item.get("price")
            if key and key not in seen:
                deduplicated_items.append(item)
                seen.add(key)

        # items = filtered_items

        print(f'deduplicated_items = {deduplicated_items}')
        selected_price_ids = {item.get('price') for item in deduplicated_items if 'price' in item}
        if (
            envs.STRIPE_STARTER_PRICE_ID in selected_price_ids and
            envs.STRIPE_IMAGE_GENERATION_PRICE_ID in selected_price_ids
        ):
            raise HTTPException(status_code=404, detail="You are not allowed to subscribe to image generation addon with starter plan.")
        updated_subscription = stripe.Subscription.modify(
            subscription_id,
            items=deduplicated_items,
            proration_behavior="create_prorations",
            billing_cycle_anchor="now",
            payment_behavior="allow_incomplete",
            discounts=[{"coupon": promo_codes.data[0].coupon}] if request.coupon else None,
            default_payment_method=default_payment_method,
            expand=["latest_invoice"]
        )
        #  If no payment is required
        await insert_logs(user.organization_id, f"Organization has been successfully updated subscription.", f'{user.name}', "Subscription", db)

        return {
            "message": "Your plan has been updated successfully."
        }
    
    # except Exception as e:
    #     raise HTTPException(status_code=404, detail="Failed to update payment")


import time
from app.services.payment import is_base_plan

async def is_yearly_plan(price_id):
    """
    Check if a given price ID represents a yearly plan by fetching from Stripe
    """
    try:
        price = await stripe.Price.retrieve_async(price_id)
        if price.recurring and price.recurring.get("interval") == "year":
            return True
        return False
    except Exception as e:
        print(f"Error checking yearly plan for price_id {price_id}: {e}")
        return False
    
    
@payments_router_protected.post("/preview-invoice")
async def preview_invoice(
    data: UpdateSubscriptionRequest,
    user: User = Depends(get_current_user)
):
    try:
        from app.services.payment import get_current_user_seats

        total_users_paid = await get_current_user_seats(stripe_customer_id = user.stripeId)
        if data.priceId in [envs.STRIPE_ENTERPRISE_PRICE_ID_YEARLY, envs.STRIPE_ENTERPRISE_PRICE_ID_MONTHLY] and (total_users_paid + data.no_of_users_apart_from_admin) < 100:
            raise HTTPException(status_code=400, detail="You must have at least 100 users to subscribe to enterprise plan.")

        # Fetch the promo code from Stripe
        if data.coupon:
            promo_codes = await stripe.PromotionCode.list_async(
                code=data.coupon,
                active=True,
                limit=1,
            )

            if not promo_codes.data or not promo_codes.data[0].coupon:
                raise HTTPException(status_code=404, detail="Promo code is invalid.")

        ADD_ON_PRICE_MAP = {"image_generation":envs.STRIPE_IMAGE_GENERATION_PRICE_ID}
        print(f'one')
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="User has no Stripe ID.")
        subscriptions = await stripe.Subscription.list_async(
            customer=user.stripeId,
            # status="active", # default to active
            limit=1,
            expand=["data.items"]
        )

        if not subscriptions:
            subscriptions = await stripe.Subscription.list_async(
                customer=user.stripeId,
                status="canceled",
                limit=1,
                expand=["data.items"]
            )
            if not subscriptions:
                raise HTTPException(status_code=404, detail="No valid subscription found to update.")
            
            if subscriptions.data[0].status == "canceled":
                user_susbcription_items = [
                        {"price": data.priceId}
                    ] + [
                        {"price": price_id}
                        for key, enabled in data.add_on.items()
                        if enabled and (price_id := ADD_ON_PRICE_MAP.get(key))
                    ]
                selected_price_ids = {item.get('price') for item in user_susbcription_items if 'price' in item}
                if (
                    envs.STRIPE_STARTER_PRICE_ID in selected_price_ids and
                    envs.STRIPE_IMAGE_GENERATION_PRICE_ID in selected_price_ids
                ):
                    raise HTTPException(status_code=404, detail="You are not allowed to subscribe to image generation addon with starter plan.")
                seen = set()
                filtered_items = []
                for item in user_susbcription_items:
                    key = item.get("id") or item.get("price")
                    if key and key not in seen:
                        filtered_items.append(item)
                        seen.add(key)

                user_susbcription_items = filtered_items
                invoice =  stripe.Invoice.create_preview(
                customer=user.stripeId,
                subscription_details={
                    "items": user_susbcription_items,
                    "proration_behavior": "none",
                },
                discounts=[{"coupon": promo_codes.data[0].coupon}] if data.coupon else None,

                preview_mode="next"  # or "recurring" depending on your use case
                )
                line_items = []
                proration_total = 0
                for line in invoice.lines.data:
                    line_items.append({
                        "description": line.description,
                        "amount": line.amount / 100,  # Convert cents to dollars
                        "period": {
                            "start": line.period.start,
                            "end": line.period.end
                        },
                        "proration": True #there will no proration as we technically creating new invoie.
                    })
                    proration_total += line.amount
                return {
                        "success": True,
                        "invoice": {
                            "total": proration_total / 100,
                            "subtotal": proration_total / 100,
                            "due_now": proration_total / 100,
                            "next_billing_date": invoice.next_payment_attempt,
                            "line_items": line_items
                        }
                    }
        if not subscriptions:
            raise HTTPException(status_code=404, detail="No valid subscription found to update.")
        
        # Get current subscription - using async version to be consistent
        subscription = await stripe.Subscription.retrieve_async(
            subscriptions.data[0].id,
            expand=["items"]
        )
        
        # print(f'response subscription =  {subscription}')
        # Check for yearly to monthly downgrade prevention
        # Check for yearly to monthly downgrade prevention
        current_base_plan_item = next(
            (item for item in subscription['items']['data'] 
             if is_base_plan(item.price.id)), 
            None
        )
        if current_base_plan_item and current_base_plan_item.price.recurring.get("interval") == "year":
            # Get the new plan's interval
            new_plan_price = await stripe.Price.retrieve_async(data.priceId)
            if new_plan_price.recurring and new_plan_price.recurring.get("interval") == "month":
                raise HTTPException(
                    status_code=400, 
                    detail="Cannot downgrade from yearly to monthly plan. Please cancel your current subscription and wait for it to expire before subscribing to a monthly plan."
                )
            # raise HTTPException(
            #     status_code=400, 
            #     detail="Cannot downgrade from yearly to monthly plan. Please cancel your current subscription and wait for it to expire before subscribing to a monthly plan."
            # )
        
        # Map current subscription items
        current_items = {item.price.id: item.id for item in subscription['items']['data']}
        # Prepare items for update preview
        items = []
        print(f'three current_items = {current_items}')
       
        # Add base plan
        if data.priceId in current_items:
            # Update existing item
            items.append({"id": current_items[data.priceId]})
        else:
            # If changing base plan, remove old one and add new one
            base_item_id = next((item.id for item in subscription['items']['data']
                               if is_base_plan(item.price.id)), None)
            if base_item_id:
                items.append({"id": base_item_id, "deleted": True})
            items.append({"price": data.priceId})
        print(f'fial =four == {items}')
        
        # Create a reverse map of enabled add-on price IDs
        selected_addon_price_ids = {
            price_id
            for key, enabled in data.add_on.items()
            if enabled and (price_id := ADD_ON_PRICE_MAP.get(key))
        }

        # Remove existing add-ons that are no longer selected
        for price_id, item_id in current_items.items():
            if is_base_plan(price_id):
                continue
            if price_id not in selected_addon_price_ids:
                items.append({"id": item_id, "deleted": True})
        # Add new add-ons that aren't already in the subscription
        for addon_price_id in selected_addon_price_ids:
            if addon_price_id not in current_items:
                items.append({"price": addon_price_id})

         
        selected_price_ids = {item.get('price') for item in items if 'price' in item}
        if (
            envs.STRIPE_STARTER_PRICE_ID in selected_price_ids and
            envs.STRIPE_IMAGE_GENERATION_PRICE_ID in selected_price_ids
        ):
            raise HTTPException(status_code=404, detail="You are not allowed to subscribe to image generation addon with starter plan.")
        if data.no_of_users_apart_from_admin is not None and data.user_usage_priceId:
                # 1. delete any existing user-item
                
                for item in subscription["items"]["data"]:
                    print(f'items price ids == {item.price.id}')
                    if not is_base_plan(item.price.id):
                            print(f'usage userage plan deltion occure')
                            items.append({"id": item.id, "deleted": True})
                            
                
                # 2. insert the yearly (or monthly) user price
                print(f'quantity == {data.no_of_users_apart_from_admin}')
                items.append({
                    "price": data.user_usage_priceId,
                    "quantity": max(0, data.no_of_users_apart_from_admin)
                })
                # # 1. Remove the *any* existing user-item (monthly or yearly)
                # user_item = next(
                #     (item for item in subscription["items"]["data"]
                #     if not is_base_plan(item.price.id)),   # all non-base items
                #     None
                # )
                # if user_item:
                #     items.append({"id": user_item.id, "deleted": True})
                # # 2. Add the new yearly (or monthly) user price
                # items.append({
                #     "price": data.user_usage_priceId,
                #     "quantity": max(0, data.no_of_users_apart_from_admin)
                # })

                # if existing_usage_item:
                #     # Update quantity on existing item
                #     items.append({
                #         "id": existing_usage_item.id,
                #         "quantity": data.no_of_users_apart_from_admin
                #     })
                # else:
                #     # Price not in subscription yet → add it
                #     items.append({
                #         "price": data.user_usage_priceId,
                #         "quantity": data.no_of_users_apart_from_admin
                #     })

        # # total_paid_this_cycle_cents = await calculate_current_cycle_paid(user.stripeId)
        # print(f'subscription printed 0 == {subscription}')
         
        print(f'final before pervie == {items}')
        # remove duplicates while keeping the first occurrence
        seen = set()
        filtered_items = []
        for item in items:
            key = item.get("id") or item.get("price")
            if key and key not in seen:
                filtered_items.append(item)
                seen.add(key)

        items = filtered_items
        print(f'after removing uplicates == {items}')
        invoice_with_preview =  stripe.Invoice.create_preview(
                customer=user.stripeId,
                subscription = subscription.id,
                subscription_details={
                    "items": items,
                    "proration_behavior": "create_prorations",
                    "billing_cycle_anchor":"now"
                },
                discounts=[{"coupon": promo_codes.data[0].coupon}] if data.coupon else None,

                preview_mode="next"  # or "recurring" depending on your use case
                )
        
        # Format the response
        line_items = []
        # proration_total = 0   
        for line in invoice_with_preview.lines.data:
            if line.proration:
                line_items.append({
                    "description": line.description,
                    "amount": line.amount / 100,  # Convert cents to dollars
                    "period": {
                        "start": line.period.start,
                        "end": line.period.end
                    },
                    "proration": True
                }) 
                # proration_total += line.amount
        # print(f'final reust = {invoice.total / 100}')
        # print(f'due now {invoice.amount_due / 100}')
        print(f'final invoice reust = {invoice_with_preview.total / 100}')
        print(f'due invoice now {invoice_with_preview.amount_due / 100}')
        
        return {
            "success": True,
            "invoice": {
                "total": invoice_with_preview.amount_due / 100,
                "subtotal": invoice_with_preview.amount_due / 100,
                "due_now": invoice_with_preview.amount_due / 100,
                "next_billing_date": invoice_with_preview.next_payment_attempt,
                "line_items": line_items
            }
        }
    except Exception as e:
        print(f"Error in preview_invoice: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    

@payments_router_protected.get("/create-payment-session")
async def create_payment_session(priceId: str, user: User = Depends(get_current_user)):
    print('Stripe ID', user.stripeId)
    try:
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="Stripe ID not found")
        
        subscriptions = await stripe.Subscription.list_async(customer=user.stripeId, status="active")
        print(f'subscriptions == {subscriptions}')
        if subscriptions.data:
            session = await stripe.billing_portal.Session.create_async(
                customer=user.stripeId,
                return_url=f"{envs.FRONTEND_HOST}/"  # User will return here after managing their subscription
            )
            return {"checkout_url": session.url}
 
        client_secret = await payment.create_checkout_session(user.id, user.stripeId, priceId)
        
        return {"checkout_url": client_secret.url} 
    except HTTPException as http_exc:
        # This will handle our custom raised HTTPExceptions
        logger.exception(http_exc)
        raise http_exc    
    except Exception as e:
        logger.exception(e)
        raise HTTPException(status_code=500, detail="Server error")
    
from datetime import datetime

@payments_router_protected.post("/cancel-subscription")
async def cancel_subscription(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_async_db),
    db = Depends(get_async_db)
    # cancel_at_period_end: bool = True
):
    try:
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="User has no Stripe ID.")
        # Get user's active subscription
        subscriptions = await stripe.Subscription.list_async(
            customer=user.stripeId,
            # status="active",
            limit=1,
            expand=["data.items"]
        )
        # If still empty, raise error
        if not subscriptions.data:
            raise HTTPException(status_code=404, detail="No valid subscription found to update.")
        print(f'cancel here = {subscriptions}')
        subscription_data = subscriptions.data[0]

        if subscription_data.status != "active":
            previous_base_price = None
            user.current_plan
            if user.current_plan == Plan.starter:
                previous_base_price = envs.STRIPE_STARTER_PRICE_ID 
            elif user.current_plan == Plan.enterprise:
                previous_base_price = envs.STRIPE_ENTERPRISE_PRICE_ID 
            if not previous_base_price:
                raise HTTPException(status_code=400, detail="You dont have any active subscription to cancel")
        
            original_items = [{
                "id": subscription_data["items"]["data"][0]["id"],
                "price": previous_base_price
            }]
            print(f'addon in db == {user.add_on_features}')
            # Include the add-on if enabled in DB
            if user.add_on_features:
                if "image_generation" in user.add_on_features:
                    print(f'image generation present')
                    original_items.append({
                        "price": envs.STRIPE_IMAGE_GENERATION_PRICE_ID
                    })
            
            seen = set()
            deduplicated_items = []
            for item in original_items:
                key = item.get("id") or item.get("price")
                if key and key not in seen:
                    deduplicated_items.append(item)
                    seen.add(key)

            original_items = deduplicated_items
            stripe.Subscription.modify(
                subscription_data.id,
                items=original_items,
                proration_behavior="none",
                expand=["latest_invoice"]
            )
            # subscriptions = stripe.Subscription.list(
            #     customer=user.stripeId,
            #     status="active",
            #     limit=1,
            #     expand=["data.items"]
            # )
            # print(f'updated subscriptions == {subscriptions}')
            # subscription_data = subscriptions.data[0]
            # print(f'after update')
            # if subscription_data.status != "active":
            #     print(f'no active presetn')
            #     raise HTTPException(status_code=400, detail="please first create subscription")
        

        # if not subscriptions.data:
        #     raise HTTPException(status_code=404, detail="No active subscription found")
        # subscription_id = subscriptions.data[0].id

        cancelled_subscriptions = []
        for sub in subscriptions.data:
            print(f'addasdsddf')
            # Schedule subscription to cancel at period end
            updated_sub = await stripe.Subscription.modify_async(
                sub.id,
                cancel_at_period_end=True
            )

            # Extract cancel timestamp
            cancel_at_timestamp = updated_sub.get("cancel_at")
            formatted_cancel_date = datetime.utcfromtimestamp(cancel_at_timestamp).strftime("%Y-%m-%d %H:%M:%S UTC") if cancel_at_timestamp else None

            # Get the plan info from the first subscription item
            first_item = updated_sub["items"]["data"][0] if updated_sub["items"]["data"] else None
            price = first_item["price"] if first_item else None

            plan_id = price["id"] if price else "N/A"

            cancelled_subscriptions.append({
                "subscription_id": updated_sub.id,
                "plan_id": plan_id,
                "cancel_at": formatted_cancel_date,
            })
        
        # Detach the default payment method after canceling subscription
        # customer =  stripe.Customer.retrieve(user.stripeId)
        # default_pm = customer.invoice_settings.default_payment_method

        # if default_pm:
        #     await stripe.PaymentMethod.detach(default_pm)
        await insert_logs(user.organization_id, f"Organization has been successfully canceled subscription.", f'{user.name}', "Subscription", db)
    
        return {
            "message": "Your subscription(s) will be cancelled at the end of the billing period.",
            "plans_to_cancel": cancelled_subscriptions,
            "subscription": subscriptions
        }
        # Cancel or schedule cancellation
        # subscription = await stripe.Subscription.modify_async(
        #     subscription_id,
        #     cancel_at_period_end=True
        # )
        # cancel_at_timestamp = subscription.get("cancel_at")
        # if cancel_at_timestamp:
        #     cancel_date = datetime.utcfromtimestamp(cancel_at_timestamp)
        #     formatted_date = cancel_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        #     print(f"Subscription will cancel on: {formatted_date}")

        # message = (
        #     "Your subscription will be cancelled at the end of the billing period."
        #     # if cancel_at_period_end else
        #     # "Your subscription has been cancelled immediately."
        # )

        # return {"message": message, "cancel_at": formatted_date,"subscription": subscription}

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@payments_router_protected.post("/resume-subscription")
async def resume_subscription(user: User = Depends(get_current_user), db = Depends(get_async_db)):
    try:
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="Stripe ID not found.")
        
        # Get subscriptions that are scheduled to cancel
        subscriptions = await stripe.Subscription.list_async(
            customer=user.stripeId,
            status="active",
            expand=["data.items"],
            limit=1
        )

        if not subscriptions.data:
            raise HTTPException(status_code=404, detail="No active subscription found.")

        subscription = subscriptions.data[0]

        # Check if subscription is scheduled to cancel
        if not subscription.cancel_at_period_end:
            return {"detail": "Subscription is not scheduled to cancel."}

        # Resume the subscription (remove cancel schedule)
        updated_subscription = await stripe.Subscription.modify_async(
            subscription.id,
            cancel_at_period_end=False
        )
        await insert_logs(user.organization_id, f"Organization has been successfully resume subscription.", f'{user.name}', "Subscription", db)
    
        return {
            "message": "Subscription cancellation has been removed.",
            "subscription": updated_subscription
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@payments_router_protected.post("/update-card")
async def update_card(
    request: UpdateCardRequest,
    user: User = Depends(get_current_user)
):
    try:
        if not user.stripeId:
            raise HTTPException(status_code=400, detail="Stripe customer ID not found.")

        # Step 1: Attach the new payment method to the customer
        await stripe.PaymentMethod.attach_async(
            request.payment_method_id,
            customer=user.stripeId
        )

        # Step 2: Set it as the default payment method
        await stripe.Customer.modify_async(
            user.stripeId,
            invoice_settings={
                "default_payment_method": request.payment_method_id
            }
        )

        return {
            "message": "Card updated successfully.",
            "payment_method_id": request.payment_method_id
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=e.user_message or str(e))        

async def calculate_current_cycle_paid(user_stripe_id: str):
    subscriptions = await stripe.Subscription.list_async(
        customer=user_stripe_id,
        status="active",
        limit=1
    )
    if not subscriptions.data:
        return 0

    subscription = subscriptions.data[0]
    period_start = subscription.current_period_start
    invoices = await stripe.Invoice.list_async(
        customer=user_stripe_id,
        limit=100,
        status="paid"
    )
    print(f'invoice got')
    for inv in invoices.data:
        print(f'inv.created = {inv.created} period_start = {period_start} and date checks {inv.created >= period_start}')
    cycle_invoices = [
        inv for inv in invoices.data if inv.created >= period_start
    ]
    sorted_invoices = sorted(cycle_invoices, key=lambda inv: inv.created, reverse=True)
    print(f'total invoices = {len(sorted_invoices)}')
    invoices_to_sum = sorted_invoices[1:]
    print(f'invoice consdiered = {len(invoices_to_sum)}')
    for inv in invoices_to_sum:
        print(f'inoice  === {inv}')
    total_paid_cents = sum(inv.amount_paid for inv in invoices_to_sum)
    return total_paid_cents