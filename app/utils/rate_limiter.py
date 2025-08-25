from fastapi import HTTPException, Request
from redis import Redis
from datetime import datetime, timedelta
import time

from app.common.env_config import get_envs_setting
envs = get_envs_setting()


class AppRateLimiter:
    # def __init__(self, redis_client):
    #     self.redis = redis_client
    #     self.app_requests_per_x_seconds = envs.APP_REQUESTS_PER_X_SECONDS
    #     self.app_tokens_per_x_seconds = envs.APP_TOKENS_PER_X_SECONDS

    def __init__(self):
        self.redis = None  # Initially set to None
        self.app_requests_per_x_seconds = envs.APP_REQUESTS_PER_X_SECONDS
        self.app_tokens_per_x_seconds = envs.APP_TOKENS_PER_X_SECONDS
        self.app_file_uploads_per_x_seconds = envs.APP_UPLOADS_PER_X_SECONDS


    async def init_redis(self):
        """Initialize redis client only if it's not already set"""
        if self.redis is None:
            from app.services.user_chat import redis_store
            self.redis = redis_store

    async def check_chat_limits(self):
        # current_minute = int(time.time() / 60)
        app_req_key = f"public:app:requests"
        app_tokens_key = f"public:app:tokens"
        # print(f"Current app_req key  {app_req_key} \t {app_tokens_key}\n")

        # older_key = "app:tokens:28935952"
        # print(f"\nAn older key token are : {self.redis.get(older_key)}\n")

        app_requests = int(await self.redis.get(app_req_key) or 0)
        app_tokens = int(await self.redis.get(app_tokens_key) or 0)
        
        print(f"\nRL: App requests: {app_requests}, App tokens: {app_tokens}\n App Token limit expiry: {await self.redis.ttl('public:app:requests')}, App Request limit expiry: {await self.redis.ttl('public:app:tokens')}\n")
        if app_requests >= self.app_requests_per_x_seconds:
            ttl = await self.redis.ttl("public:app:requests")

            if ttl == -1:
                print("RL:  App key has no associated expiration.")
                ttl = 5
            elif ttl == -2:
                print("RL:  App key does not exist.")
                ttl = 5
            else:
                # seconds_till_next_minute = 60 - (time.time() % 60)
                print(f"RL:  App request key will expire in {ttl} seconds.")
        
            raise HTTPException(status_code=429, detail=f"We are experiening heavy request load. Please try again after {ttl} seconds.")
            
        if app_tokens> self.app_tokens_per_x_seconds:
            ttl = await self.redis.ttl(f"public:app:tokens")

            if ttl == -1:
                ttl = 5
                print("RL:  App key has no associated expiration.")
            elif ttl == -2:
                ttl = 5
                print("RL:  App key does not exist.")
            else:
                # seconds_till_next_minute = 60 - (time.time() % 60)
                print(f"RL:  App token key will expire in {ttl} seconds.")

            raise HTTPException(status_code=429, detail=f"Application token limit exceeded. Please try again after {ttl} seconds.")
        
        if not await self.redis.exists(app_req_key):
            await self.redis.incr(app_req_key)
            await self.redis.expire(app_req_key, envs.APP_KEY_DURATION_SECONDS)
        else:
            await self.redis.incr(app_req_key)

    
    async def check_url_limits(self):
        """Checks application-wide URL request limits"""

        url_req_key = "public:app:url_requests"

        url_requests = int(await self.redis.get(url_req_key) or 0)

        print(f"\nRL: URL requests: {url_requests}\n URL Request limit expiry: {await self.redis.ttl(url_req_key)}\n")
        
        if url_requests >= self.app_file_uploads_per_x_seconds:
            ttl = await self.redis.ttl(url_req_key)
            if ttl == -1:
                ttl = 5
                print("RL:  URL request key has no associated expiration.")
            elif ttl == -2:
                ttl = 5
                print("RL:  URL request key does not exist.")
            else:
                # seconds_till_next_minute = 60 - (time.time() % 60)
                print(f"RL:  URL request key will expire in {ttl} seconds.")

            raise HTTPException(status_code=429, detail=f"Too many URL requests. Please try again after {ttl} seconds.")

        if not await self.redis.exists(url_req_key):
            await self.redis.incr(url_req_key)
            await self.redis.expire(url_req_key, envs.APP_FILEUPLOADS_KEY_DURATION_SECONDS)
        else:
            await self.redis.incr(url_req_key)