"""Minimal Hugging Face Space demo for the localized NFA-ViT signal."""

from __future__ import annotations

import os

import gradio as gr
from PIL import Image

from runtime import NfaVitRuntime

runtime = NfaVitRuntime()


def predict(image: Image.Image):
    if image is None:
        raise gr.Error("Please upload an image.")
    return runtime.predict(image)


demo = gr.Interface(
    fn=predict,
    inputs=gr.Image(type="pil", label="Image"),
    outputs=[
        gr.JSON(label="Normalized NFA-ViT signal"),
        gr.Image(type="pil", label="Localized candidate regions"),
    ],
    title="NFA-ViT localized manipulation signal",
    description=(
        "Detects possible localized AI-generated/inpainted regions. "
        "This signal is not proof that an image is fake and does not detect deepfakes."
    ),
    api_name="predict",
)

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("NFA_VIT_SERVER_PORT", "7860")),
    )
