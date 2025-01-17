from argparse import ArgumentParser
from urllib.request import urlopen

import torch
from lightning import Trainer, seed_everything
from torch.utils.data import DataLoader

from lightning_gpt import callbacks, data, models


def main(args):
    with open("src/lightning-gpt/quora_qa_dataset.txt") as f:
        text = f.read()

    train_dataset = data.CharDataset(text, args.block_size)

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, num_workers=args.num_workers)

    GPT_class = None
    extra_kwargs = {}

    if args.implementation == "mingpt":
        GPT_class = models.MinGPT
        extra_kwargs.update(
            dict(
                embd_pdrop=0.1,
                resid_pdrop=0.1,
                attn_pdrop=0.1,
            )
        )

    elif args.implementation == "nanogpt":
        GPT_class = models.NanoGPT
        extra_kwargs["dropout"] = 0.1

    else:
        raise ValueError(f"Unsupported implementation {args.implementation}")

    if args.strategy == "deepspeed":
        if GPT_class == models.MinGPT:
            GPT_class = models.DeepSpeedMinGPT
        elif GPT_class == models.NanoGPT:
            GPT_class = models.DeepSpeedNanoGPT
        else:
            raise ValueError(f"Implementation {args.implementation} not supported with DeepSpeed")
        extra_kwargs["offload"] = False

    elif args.strategy == "fsdp_native":
        if GPT_class == models.MinGPT:
            GPT_class = models.FSDPMinGPT
        elif GPT_class == models.NanoGPT:
            GPT_class = models.FSDPNanoGPT
        else:
            raise ValueError(f"Implementation {args.implementation} not supported with FSDP")

    model = GPT_class(
        vocab_size=train_dataset.vocab_size,
        block_size=train_dataset.block_size,
        model_type=args.model_type,
        n_layer=args.n_layer,
        n_head=args.n_head,
        n_embd=args.n_embd,
        weight_decay=0.1,
        learning_rate=args.learning_rate,
        betas=(0.9, 0.95),
        **extra_kwargs,
    )

    if args.compile:
        if not hasattr(torch, "compile"):
            raise RuntimeError(
                f"The current torch version ({torch.__version__}) does not have support for compile."
                "Please install torch >= 1.14 or disable compile."
            )
        model = torch.compile(model)

    callback_list = []

    if torch.cuda.is_available():
        torch.set_float32_matmul_precision("high")
        callback_list.append(callbacks.CUDAMetricsCallback())

    trainer = Trainer(
        max_epochs=args.max_epochs,
        gradient_clip_val=1.0,
        precision=args.precision,
        enable_progress_bar=args.enable_progress_bar,
        strategy=args.strategy,
        accelerator=args.accelerator,
        devices=args.devices,
    )

    trainer.fit(model, train_loader)

    context = "Friends of my soul"  # Prime with something
    x = train_dataset.to_tokens(context, model.device)
    y = model.generate(x, max_new_tokens=1000, temperature=1.0, do_sample=True, top_k=10)
    print(train_dataset.from_tokens(y))


if __name__ == "__main__":
    seed_everything(42)

    parser = ArgumentParser()
    # parser = Trainer.add_argparse_args(parser)

    parser.add_argument("--model_type", default="gpt2", type=str)
    parser.add_argument("--n_layer", type=int)
    parser.add_argument("--n_head", type=int)
    parser.add_argument("--n_embd", type=int)
    parser.add_argument("--learning_rate", default=3e-4, type=float)
    parser.add_argument("--block_size", default=128, type=int)
    parser.add_argument("--batch_size", default=64, type=int)
    parser.add_argument("--num_workers", default=8, type=int)
    parser.add_argument("--compile", default=None, choices=[None, "dynamo"])
    parser.add_argument("--implementation", default="mingpt", choices=["mingpt", "nanogpt"])
    parser.add_argument("--strategy", default="auto")
    parser.add_argument('--max_epochs', default=10, type=int)
    parser.add_argument('--precision', default=16, type=int)
    parser.add_argument('--enable_progress_bar', default=True, type=bool)
    parser.add_argument('--accelerator', default="gpu")
    parser.add_argument('--devices', default=1, type=int)

    args = parser.parse_args()

    main(args)
