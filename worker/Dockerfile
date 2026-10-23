# Worker image for AWS Lambda (container image, triggered by SQS).
# Build from the repo root so settings.yaml can be copied in:
#   docker build -f worker/Dockerfile -t btr-worker .
# worker/Dockerfile.dockerignore allowlists the build context, so .env.local never enters it.
FROM public.ecr.aws/lambda/python:3.12

# Install pinned, hash-checked dependencies first so this layer caches.
COPY worker/requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY settings.yaml ${LAMBDA_TASK_ROOT}/settings.yaml
COPY worker/app/ ${LAMBDA_TASK_ROOT}/app/
ENV SETTINGS_PATH=${LAMBDA_TASK_ROOT}/settings.yaml

CMD ["app.handler.lambda_handler"]
