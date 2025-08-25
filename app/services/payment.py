from app.common.env_config import get_envs_setting
import stripe
from stripe import Subscription
import logging
from fastapi import HTTPException
from app.models.user import Plan, User, UserRole
from app.models.chatbot_model import ChatbotConfig
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.future import select
from sqlalchemy.orm.attributes import flag_modified


logger = logging.getLogger(__name__)
settings = get_envs_setting()
# This is your test secret API key.
stripe.api_key = settings.STRIPE_SECRET_KEY

class SubscriptionStatus(BaseModel):
    plan: Plan
    status: bool
    subscription: Optional[dict] = None

# Helper function to determine if a price is a base plan
def is_base_plan(price_id):
    
    price = stripe.Price.retrieve(price_id, expand=["product"])
    plan_type = price.product.metadata.get("type", "")
    return plan_type not in {"addon", "metered_user"}
    # return price.product.metadata.get("type") != "addon" and price.product.metadata.get("type") != "metered_user"


async def create_customer(user: User):
    try:
        # Create a new customer in Stripe
        stripe_customer = await stripe.Customer.create_async(
            email=user.email,
            name=user.name,
            description="Customer for {}".format(user.email),
        )
        # Save the stripe customer ID in your database for future use
        # For example: update_user_with_stripe_id(user.email, stripe_customer.id)
        
        return stripe_customer.id
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

async def delete_customer(stripe_id: str):
    try:
        stripe_customer = await stripe.Customer.delete_async(sid=stripe_id)
        return stripe_customer.id
    except stripe.error.StripeError as e:
        raise Exception(f"Stripe API Error: {e}")

async def checkSubscriptionStatus(subscriptionId): 
    print(f'subscription id inside == {subscriptionId}')
    subscription = await Subscription.retrieve_async(id=subscriptionId, expand= ['items.data.price.product'])
    if(subscription):
        status = subscription["status"]
        items = subscription["items"]["data"][0]
        plan = items["price"]["product"]["name"]  
        plan_enum = Plan.from_string(plan)
        print(f'plan enum == {plan_enum} -with status- {status}')
        if(status == "canceled"):
            return SubscriptionStatus(plan=Plan.free, status=False, subscription=subscription)
        else:
            return SubscriptionStatus(plan=plan_enum, status=True, subscription=subscription)
    else:
        raise HTTPException(status_code=400, detail="Subscription was not found")

async def create_checkout_session(userId, customer_id, price_id):
    try:
        # Attempt to create a checkout session
        session = await stripe.checkout.Session.create_async(
            customer=customer_id,
            payment_method_types=['card'],  # Add or remove as per your requirement
            line_items=[{
                'price': price_id,  # Price ID passed from the frontend
                'quantity': 1,
            }],
            client_reference_id= userId,
            mode='subscription',
            success_url=f"{settings.FRONTEND_HOST}payment/success",
            cancel_url=f"{settings.FRONTEND_HOST}payment/failure",
        )
        # Check if the session is successfully created
        if not session:
            raise HTTPException(status_code=404, detail="Failed to create checkout session")
        return session
    except stripe.error.StripeError as e:
        # Handle Stripe API errors specifically
        print(f"Stripe API error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Handle other generic exceptions if needed
        print(f"An error occurred: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred")

async def update_user_image_gen_payment(customerId, subscriptionId, session):
    result = await session.execute(select(User).where(User.stripeId == customerId))
    user = result.scalars().first()
    # user = session.query(User).filter(User.stripeId == customerId).first()
    
    if not user:
        raise HTTPException(status_code=400, detail="The user does not exist!")
    
    subscription = await checkSubscriptionStatus(subscriptionId)
    print(f'plan itself == {subscription}')
    if subscription.status: 
        user.is_paid = True
        user.add_on_features.append("image_generation")
        flag_modified(user, "add_on_features")
        
    session.add(user)
    await session.commit() 
    await session.refresh(user)
    return user


async def update_user_payment(customerId, subscriptionId, session):
    result = await session.execute(select(User).where(User.stripeId == customerId))
    user = result.scalars().first()
    # user = session.query(User).filter(User.stripeId == customerId).first()
    
    if not user:
        raise HTTPException(status_code=400, detail="The user does not exist!")
    
    subscription = await checkSubscriptionStatus(subscriptionId)
    print(f'plan itself == {subscription}')
    if subscription.status: 
        user.is_paid = True
        user.current_plan = subscription.plan
    else:
        print("⚠️ No active plan found. Marking user as Free Tier.")  
        user.current_plan = Plan.free

    session.add(user)
    await session.commit() 
    await session.refresh(user)
    return user
 
async def allowed_users_checks(session, customer_id):
    # 1. Determine the correct metered user price ID based on the base plan's interval
    result = await session.execute(select(User).where(User.stripeId == customer_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=400, detail="User not found")
    organization_id = user.organization_id # Assuming user has organization_id
    
    current_max_allowed_users = 0
    # 2. Get the active subscription for the customer
    active_subscriptions = await stripe.Subscription.list_async(
        customer=customer_id,
        status="active",
        limit=1,
        expand=["data.items"] # Expand items to get prices and quantities
    )
    if active_subscriptions.data:
        subscription = active_subscriptions.data[0]
        all_active_prices = await stripe.Price.list_async(active=True, expand=['data.product'])
        metered_price_ids_by_interval = {}
        for price_obj in all_active_prices.data:
            product = price_obj.product
            # Ensure your product metadata identifies metered user products correctly
            if product and product.get("metadata", {}).get("type") == "metered_user_addon": # Use 'metered_user_addon' as agreed
                interval = price_obj.recurring.get("interval") if price_obj.recurring else None
                if interval:
                    metered_price_ids_by_interval[interval] = price_obj.id
        # Determine the base plan's interval from the current active subscription
        base_plan_item = next(
            (item for item in subscription["items"]["data"] if is_base_plan(item["price"]["id"])),
            None
        )

        metered_user_price_id_for_current_interval = None
        if base_plan_item and base_plan_item["price"]["recurring"]:
            base_plan_interval = base_plan_item["price"]["recurring"]["interval"]
            metered_user_price_id_for_current_interval = metered_price_ids_by_interval.get(base_plan_interval)

        # Find the metered user subscription item and extract its quantity
        if metered_user_price_id_for_current_interval:
            metered_user_item = next(
                (item for item in subscription["items"]["data"] 
                 if item["price"]["id"] == metered_user_price_id_for_current_interval),
                None
            )
            if metered_user_item and metered_user_item.get("quantity") is not None:
                current_max_allowed_users = metered_user_item["quantity"]
    print(f"Calculated max allowed users for customer {customer_id}: {current_max_allowed_users}")
    # Get the current count of billable users in the organization
    current_billable_users_count = await _get_total_org_users_count(session, organization_id)
    
    print(f"Current billable users in organization {organization_id}: {current_billable_users_count}")
    if (current_billable_users_count + 1) > current_max_allowed_users:
        return False # Cannot add new user
    
    return True # Can add new user    

import stripe
from typing import Optional
async def get_current_user_seats(
    stripe_customer_id: str,
    user_product_id: str = "prod_SgxPuKhmZlII1x"
) -> int:
    if not stripe_customer_id:
        return 0

    subs = await stripe.Subscription.list_async(
        customer=stripe_customer_id,
        status="active",
        limit=1,
        expand=["data.items.data.price"]  # 3 levels only
    )
    if not subs.data:
        return 0

    for item in subs.data[0]["items"]["data"]:
        # price.product is a string (the product id) when not expanded
        if item.price.product == user_product_id:
            return item.quantity or 0
    return 0
async def update_payment_details(customer_id, subscription_id, session):
    result = await session.execute(select(User).where(User.stripeId == customer_id))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # subscription = await checkSubscriptionStatus(subscription_id)
    subscription_status = await checkSubscriptionStatus(subscription_id)
    subscription = subscription_status.subscription

    if subscription_status.status:
        user.is_paid = True
        user.current_plan = subscription_status.plan

        # Detect image generation add-on by checking line items
        # has_image_gen = any(
        #     item.price.id == settings.STRIPE_IMAGE_GENERATION_PRICE_ID
        #     for item in subscription.items.data
        # )
        if user.add_on_features is None:
            user.add_on_features = []
            flag_modified(user, "add_on_features")
            
        has_image_gen = any(
            item["price"]["id"] == settings.STRIPE_IMAGE_GENERATION_PRICE_ID
            for item in subscription["items"]["data"]
        )

        # Update add-on features list
        if has_image_gen and "image_generation" not in user.add_on_features:
            user.add_on_features.append("image_generation")
            flag_modified(user, "add_on_features")
            print(f'image generation added to db')
        elif not has_image_gen and "image_generation" in user.add_on_features:
            user.add_on_features.remove("image_generation")
            flag_modified(user, "add_on_features")
        # --- NEW LOGIC FOR METERED USER COUNT ---
        # 1. Determine the correct metered user price ID based on the base plan's interval
        new_max_users_from_stripe = 0
        all_active_prices = await stripe.Price.list_async(active=True, expand=['data.product'])
        metered_price_ids_by_interval = {}
        for price_obj in all_active_prices.data:
            product = price_obj.product
            # Assuming you set metadata on your product/price for metered users
            if product and product.get("metadata", {}).get("type") == "metered_user":
                interval = price_obj.recurring.get("interval") if price_obj.recurring else None
                if interval:
                    metered_price_ids_by_interval[interval] = price_obj.id

        # 1. Determine the correct metered user price ID based on the base plan's interval
        base_plan_item = next((item for item in subscription["items"]["data"] if is_base_plan(item["price"]["id"])), None)
        
        metered_user_price_id_for_current_interval = None
        if base_plan_item and base_plan_item["price"]["recurring"]:
            base_plan_interval = base_plan_item["price"]["recurring"]["interval"]
            metered_user_price_id_for_current_interval = metered_price_ids_by_interval.get(base_plan_interval)
        
        # 2. Find the subscription item for the determined metered user price and extract its quantity
        if metered_user_price_id_for_current_interval:
            metered_user_item = next(
                (item for item in subscription["items"]["data"] 
                 if item["price"]["id"] == metered_user_price_id_for_current_interval),
                None
            )
            if metered_user_item and metered_user_item.get("quantity") is not None:
                new_max_users_from_stripe = metered_user_item["quantity"]
        # 3. Update your User (or Organization) model with the new max allowed users
        print(f'Updated max users == {new_max_users_from_stripe}')
        # from app.services.organization import get_organization_users
        # users =  await get_organization_users(session, user.organization_id,  role_filter=UserRole.USER, skip=0, limit=100000)
    
        current_billable_users_count = await _get_total_org_users_count(session, user.organization_id)
        # Check if the current number of billable users exceeds the new allowed limit
        if current_billable_users_count > new_max_users_from_stripe:
            # Retrieve *only* the oldest users that need to be removed
            users_to_remove = await _get_oldest_org_users(session, user.organization_id, current_billable_users_count - new_max_users_from_stripe)

            for user_to_remove in users_to_remove:
                print(f"Removing user {user_to_remove.id} ({user_to_remove.email}) due to plan downgrade.")
                from app.services.organization import remove_user_from_organization_service
                for user_to_remove in users_to_remove:
                    await remove_user_from_organization_service(session, user.organization_id, user_to_remove.id, user)
                # You might want to log this action or notify the organization admin

        # flag_modified(user, "add_on_features") 

    else:
        user.current_plan = Plan.free
        user.is_paid = False
        user.add_on_features.clear()
        current_billable_users_count = await _get_total_org_users_count(session, user.organization_id)
        users_to_remove = await _get_oldest_org_users(session, user.organization_id, current_billable_users_count)
        for user_to_remove in users_to_remove:
                print(f"Removing user {user_to_remove.id} ({user_to_remove.email}) due to plan downgrade.")
                from app.services.organization import remove_user_from_organization_service
                for user_to_remove in users_to_remove:
                    await remove_user_from_organization_service(session, user.organization_id, user_to_remove.id, user)
        flag_modified(user, "add_on_features")

    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

async def handle_invoice_update(data_object, session):
    customer_id = data_object.get("customer")
    subscription_id = data_object.get("subscription")

    if not customer_id or not subscription_id:
        return  # Exit early if required fields are missing

    result = await session.execute(select(User).where(User.stripeId == customer_id))
    user = result.scalars().first()

    if not user:
        return

    if data_object["status"] == "paid":
        user.is_paid = True
    elif data_object["status"] == "unpaid":
        print(f'failed payments')
        active_subs = await stripe.Subscription.list(customer=user.stripeId, status="active")
        if not active_subs.data:
            print('no active plans')
            user.is_paid = False
            # user.current_plan = Plan.free
            # user.add_on_features.clear()
            # flag_modified(user, "add_on_features")

    session.add(user)
    await session.commit()
    await session.refresh(user)

from sqlalchemy.ext.asyncio import AsyncSession

async def adjust_tier_degration_change(
        db: AsyncSession,
        current_user: User,
):
    from app.services.organization import remove_user_from_organization_service, remove_chatbot_from_organization_service
    if current_user.current_plan == Plan.free:
        chatbots = await _get_all_org_chatbots(current_user.organization_id, db)
        # users = await _get_all_org_users(current_user.organization_id, db)
        if not current_user.is_paid:
            for chatbot in chatbots:
                await remove_chatbot_from_organization_service(db, current_user.organization_id, chatbot.id, current_user)
            # for user in users:
            #     if user.role == UserRole.USER:
            #         await remove_user_from_organization_service(db, current_user.organization_id, user.id, current_user)
        if current_user.is_paid:
            chatbots_to_delete = sorted(chatbots, key=lambda x: x.id)[:-3]
            for chatbot in chatbots_to_delete:
                await remove_chatbot_from_organization_service(db, current_user.organization_id, chatbot.id, current_user)
            # users_sorted = sorted(users, key=lambda x: x.created_at, reverse=True)
            # users_to_delete = users_sorted[1:]
            # for user in users_to_delete:
            #     if user.role == UserRole.USER:
            #         await remove_user_from_organization_service(db, current_user.organization_id, user.id, current_user)
            
    elif current_user.current_plan == Plan.starter:
        chatbots = await _get_all_org_chatbots(current_user.organization_id, db)
        for chatbot in chatbots:
            await remove_chatbot_from_organization_service(db, current_user.organization_id, chatbot.id, current_user)
        # users = await _get_all_org_users(current_user.organization_id, db)
        # for user in users:
        #     if user.role == UserRole.USER:
        #         await remove_user_from_organization_service(db, current_user.organization_id, user.id, current_user)

    # elif current_user.current_plan == Plan.tier_2:
    #     chatbots = await _get_all_org_chatbots(current_user.organization_id, db)
    #     if len(chatbots) > settings.TEIR3_CHATBOTS:
    #         chatbots_to_delete = sorted(chatbots, key=lambda x: x.id)[:3]
    #         for chatbot in chatbots_to_delete:
    #             await remove_chatbot_from_organization_service(db, current_user.organization_id, chatbot.id, current_user)
    
    return


##################################################Helper###############################
async def _get_all_org_chatbots(organization_id, db):
    chatbots = await db.execute(
        select(ChatbotConfig)
        .filter(ChatbotConfig.organization_id == organization_id)
        .filter(ChatbotConfig.chatbot_type == "Internal")
    )
    return chatbots.scalars().all()

async def _get_all_org_users(organization_id, db):
    users = await db.execute(select(User).filter(User.organization_id == organization_id))
    return users.scalars().all()

from sqlalchemy import func
from typing import List

async def _get_total_org_users_count(db: AsyncSession, organization_id: int) -> int:
    """Get the total count of users in an organization, excluding admins."""
    # Assuming UserRole.ADMIN is the role you want to exclude from the count
    query = select(func.count(User.id)).filter(
        User.organization_id == organization_id,
        User.role != UserRole.ADMIN 
    )
    result = await db.execute(query)
    total_count = result.scalar_one()
    return total_count

async def _get_oldest_org_users(db: AsyncSession, organization_id: int, num_users_to_fetch: int) -> List[User]:
    """
    Retrieves the oldest users in an organization (excluding admins) based on creation date.
    This is useful for identifying users to delete during a downgrade.
    """
    query = select(User).filter(
        User.organization_id == organization_id,
        User.role != UserRole.ADMIN # Exclude admin from potential deletion
    ).order_by(User.created_at.asc()).limit(num_users_to_fetch) # Order by oldest first
    
    result = await db.execute(query)
    users = result.scalars().all()
    return users