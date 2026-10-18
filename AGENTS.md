# Take a look at this project structure; everything in here is boilerplate.
# 1. `worker`
* Take customized message from user. This can include how frequently the trader's agent is making trade, desired sector exposure, etc.
* Read recent files downloaded from s3 with prefix `collected-data/{timestamp}/`. The bucket is in setting.yaml. See if anything is relevant there.
* Scrape x.com for the user names specified in settings.yaml if nothing collected for a while since the last twitter feed was extracted. If you access the x.com api, collect tweets for the last 3 days for the accounts listed.
* Run LLM via OpenRouter using this data. Any additional data that's scraped/collected is downloaded to s3 with the prefix `collected-data/{timestamp}`. 


