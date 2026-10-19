FROM pytorch/pytorch:2.5.1-cuda11.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NFA_VIT_SOURCE_DIR=/opt/BR-Gen \
    NFA_VIT_WEIGHTS_REPO=Noihat/NFA-ViT \
    NFA_VIT_WEIGHTS_REVISION=cbc9383 \
    HF_HOME=/home/user/.cache/huggingface

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 https://github.com/clpbc/BR-Gen.git /opt/BR-Gen

RUN pip install --no-cache-dir "numpy<2" "pillow>=10,<12" "einops==0.8.1" "gradio>=5,<6" "huggingface_hub>=0.30,<2"

RUN useradd -m -u 1000 user && chmod -R a+rX /opt/BR-Gen
WORKDIR /app
COPY --chown=user app.py runtime.py ./
USER user
EXPOSE 7860
CMD ["python", "app.py"]
