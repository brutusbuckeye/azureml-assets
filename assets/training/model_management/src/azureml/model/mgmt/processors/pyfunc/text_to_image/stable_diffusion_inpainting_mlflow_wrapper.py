# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Contains MLFlow pyfunc wrapper for stable diffusion inpainting models.

Has methods to load the model and predict.
"""

import logging
import mlflow
import os
import pandas as pd
from diffusers import StableDiffusionInpaintPipeline
from config import MLflowSchemaLiterals, Tasks, MLflowLiterals, BatchConstants, DatatypeLiterals
from vision_utils import get_pil_image, process_image, get_current_device, image_to_base64, save_image

logger = logging.getLogger(__name__)


class StableDiffusionInpaintingMLflowWrapper(mlflow.pyfunc.PythonModel):
    """MLflow model wrapper for stable diffusion inpainting models."""

    def __init__(
        self,
        task_type: str,
    ) -> None:
        """Initialize model parameters for converting Huggingface Stable Diffusion inpainting model to mlflow.

        :param task_type: Task type used in training.
        :type task_type: str
        """
        super().__init__()
        self._pipe = None
        self._task_type = task_type
        self._batch_output_folder = None

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        """
        Load a MLflow model with pyfunc.load_model().

        :param context: MLflow context containing artifacts that the model can use for inference
        :type context: mlflow.pyfunc.PythonModelContext
        """
        self._batch_output_folder = os.getenv(BatchConstants.BATCH_OUTPUT_PATH, default=False)

        if self._task_type == Tasks.TEXT_TO_IMAGE_INPAINTING.value:
            try:
                model_dir = context.artifacts[MLflowLiterals.MODEL_DIR]
                self._pipe = StableDiffusionInpaintPipeline.from_pretrained(model_dir)
                self._pipe.to(get_current_device())
                logger.info("Model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load the the model. {str(e)}")
                raise
        else:
            raise ValueError(f"invalid task type {self._task_type}")

    def predict(self, context: mlflow.pyfunc.PythonModelContext, input_data: pd.DataFrame) -> pd.DataFrame:
        """
        Perform inference on the input data.

        :param context: MLflow context containing artifacts that the model can use for inference
        :type context: mlflow.pyfunc.PythonModelContext
        :param input_data: Pandas DataFrame with 3 columns named "prompt", "image", "mask_image" having text
                           input, initial image and mask image.
        :type input_data: pd.DataFrame
        :return: Pandas dataframe with corresponding generated images and NSFW flag.
                 Images in form of base64 string.
        :rtype: pd.DataFrame
        """
        # Decode the base64 image column
        images = input_data.loc[:, [MLflowSchemaLiterals.INPUT_COLUMN_IMAGE]].apply(axis=1, func=process_image)
        images = images.loc[:, 0].apply(func=get_pil_image).tolist()

        mask_images = input_data.loc[:, [MLflowSchemaLiterals.INPUT_COLUMN_MASK_IMAGE]].apply(
            axis=1, func=process_image
        )
        mask_images = mask_images.loc[:, 0].apply(func=get_pil_image).tolist()

        text_prompts = input_data.loc[:, MLflowSchemaLiterals.INPUT_COLUMN_PROMPT].tolist()

        assert len(text_prompts) == len(images) == len(mask_images), (
            f"Invalid input. Number of text prompt, image and mask image are expected to be same. "
            f"But, found text prompt length {len(text_prompts)}, image length {len(images)} and "
            f"mask_image length {len(mask_images)}"
        )

        generated_images = []
        nsfw_content = []
        if self._batch_output_folder:
            # Batch endpoint
            for image, mask_image, text_prompt in zip(images, mask_images, text_prompts):
                output = self._pipe(
                    prompt=text_prompt,
                    image=image,
                    mask_image=mask_image,
                    return_dict=True,
                )

                # Save image in batch output folder and append the image file name to generated_images list
                filename = save_image(self._batch_output_folder, output.images[0],
                                      format=DatatypeLiterals.IMAGE_FORMAT)
                generated_images.append(filename)
                nsfw_content.append(output.nsfw_content_detected[0] if output.nsfw_content_detected else None)
        else:
            # Online endpoint
            outputs = self._pipe(
                prompt=text_prompts,
                image=images,
                mask_image=mask_images,
                return_dict=True,
            )

            for img in outputs.images:
                generated_images.append(image_to_base64(img, format=DatatypeLiterals.IMAGE_FORMAT))

            nsfw_content = outputs.nsfw_content_detected

        df = pd.DataFrame(
            {
                MLflowSchemaLiterals.OUTPUT_COLUMN_IMAGE: generated_images,
                MLflowSchemaLiterals.OUTPUT_COLUMN_NSFW_FLAG: nsfw_content,
            }
        )

        return df
