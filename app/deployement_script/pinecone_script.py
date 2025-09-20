from pinecone.grpc import PineconeGRPC as Pinecone
from pinecone import ServerlessSpec
from pinecone import Pinecone, Index
# from app.common.env_config import get_envs_setting
# settings = get_envs_setting()


def custom_create_index(index_name: str, vector_diamention:int,api_key):
    pc = Pinecone(api_key=api_key)
    try:
        created_index = pc.create_index(
            name=index_name,
            dimension=vector_diamention,
            metric="cosine",
            spec=ServerlessSpec(
                cloud="aws",
                region="us-east-1"
            ),
            deletion_protection="disabled"
            )
        print(f'index {index_name}, created!')
        return
    except Exception as e:
        print(f"index: {index_name}, already exist.")
        return None  
    


def clean_pinecone_index(index_name: str, api_key: str):
    pc = Pinecone(api_key=api_key)          
    index = pc.Index(index_name)         
    # 1. discover every namespace
    stats = index.describe_index_stats()
    for ns in stats.namespaces:
        print(f"Deleting namespace: {ns!r}")
        index.delete(delete_all=True, namespace=ns)

    print("All namespaces and records removed.")


# custom_create_index("marti-ai",3072, "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

# clean_pinecone_index("marti-ai","pcsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")