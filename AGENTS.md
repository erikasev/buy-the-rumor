# Take a look at this project structure; everything in here is boilerplate.
# 1. `worker`
* Take customized message from user. This can include how frequently the trader's agent is making trade, desired sector exposure, etc.
* Read recent files downloaded from s3 with prefix `collected-data/{timestamp}/`. The bucket is in setting.yaml. See if anything is relevant there.
* Collect recent posts from the followed accounts (private config in the bucket, see worker/AGENTS.md) if nothing has been collected for a while since the last batch. When collecting, fetch the last 3 days of posts for the accounts listed.
* Run LLM via OpenRouter using this data. Any additional data that's scraped/collected is downloaded to s3 with the prefix `collected-data/{timestamp}`. 


