---
title: NFA-ViT Local Manipulation Signal
emoji: 🔎
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
---

# NFA-ViT localized manipulation demo

This isolated Space runs the NFA-ViT implementation from the official
[`clpbc/BR-Gen`](https://github.com/clpbc/BR-Gen) repository and returns a
localized manipulation score plus a visualized mask.

## Scope

NFA-ViT targets localized AI-generated/inpainted regions. It is not a general
whole-image AI provenance detector or a face deepfake detector. The returned
score is a model signal, not a calibrated probability that an image is fake.
The exposed score is the mean of the highest 1% pixel probabilities in the
localization mask; the upstream whole-image classification head is not used.
The image submitted to the Space is processed by the Space runtime.

The checkpoint is fetched at startup from the community re-upload
[`Noihat/NFA-ViT`](https://huggingface.co/Noihat/NFA-ViT), pinned by default to
revision `cbc9383`. That upload is explicitly marked unofficial. Verify model
and dependency license conditions before commercial use or broad redistribution;
the upstream source includes components with separate license terms.
In particular, the bundled DnCNN source states nonprofit-only use. Confirm that
the intended hackathon use fits the applicable terms before deployment.

## Hardware

GPU inference is preferred. CPU mode is supported for a smoke run but can be
slow at the model's 512×512 input size. Select Space hardware in its settings;
GPU hardware may incur hourly charges. Do not store credentials in this Space's
source files. Add private values through Space Secrets.

## Local build

From the repository root:

```bash
docker build -t nfa-vit-space deploy/nfa_vit_space
docker run --rm -p 7860:7860 -e HF_HOME=/tmp/hf-cache nfa-vit-space
```

To connect the Content Trust Engine to a deployed Space, install the optional
client and set `NFA_VIT_SPACE_URL`. For a private Space, also set `HF_TOKEN` in
the caller's environment. Image inputs are uploaded to that Space for inference.
