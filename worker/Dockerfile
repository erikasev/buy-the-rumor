# Worker image for AWS Lambda (container image, triggered by SQS).
FROM public.ecr.aws/lambda/python:3.12

# Install pinned, hash-checked dependencies first so this layer caches.
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --require-hashes -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY app/ ${LAMBDA_TASK_ROOT}/app/

CMD ["app.handler.lambda_handler"]
