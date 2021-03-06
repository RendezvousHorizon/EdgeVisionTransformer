"""Computes the flops needed for training/running transformer networks."""
"""Based on https://github.com/google-research/electra/blob/8a46635f32083ada044d7e9ad09604742600ee7b/flops_computation.py"""
import collections
from os import stat
from sys import path
from typing import List

# We checked this code with TensorFlow"s FLOPs counting, although we had to
# correct for this issue: https://github.com/tensorflow/tensorflow/issues/22071
# Assumptions going into the FLOPs counting
#   - An "operation" is a mathematical operation, not a machine instruction. So
#     an "exp" takes one opp like and add, even though in practice an exp
#     might be slower. This is not too bad an assumption because
#     matrix-multiplies dominate the compute for most models, so minor details
#     about activation functions don"t matter too much. Similarly, we count
#     matrix-multiplies as 2*m*n flops instead of m*n, as one might if
#     if considering fused multiply-add ops.
#   - Backward pass takes the same number of FLOPs as forward pass. No exactly
#     right (e.g., for softmax cross entropy loss the backward pass is faster).
#     Importantly, it really is the same for matrix-multiplies, which is most of
#     the compute anyway.
#   - We assume "dense" embedding lookups (i.e., multiplication by a one-hot
#     vector). On some hardware accelerators, these dense operations are
#     actually faster than sparse lookups.
# Please open a github issue if you spot a problem with this code!

# I am not sure if the below constants are 100% right, but they are only applied
# to O(hidden_size) activations, which is generally a lot less compute than the
# matrix-multiplies, which are O(hidden_size^2), so they don't affect the total
# number of FLOPs much.

# random number, >=, multiply activations by dropout mask, multiply activations
# by correction (1 / (1 - dropout_rate))
DROPOUT_FLOPS = 4

# compute mean activation (sum), computate variance of activation
# (square and sum), bias (add), scale (multiply)
LAYER_NORM_FLOPS = 5

# GELU: 0.5 * x * (1 + tanh(sqrt(2 / np.pi) * (x + 0.044715 * pow(x, 3))))
ACTIVATION_FLOPS = 8

# max/substract (for stability), exp, sum, divide
SOFTMAX_FLOPS = 5


class TransformerHparams(object):
    """Computes the train/inference FLOPs for transformers."""

    def __init__(self, h=768, l=12, s=512, v=30522, e=None, i=None, heads=None,
                 head_size=None, output_frac=0.15625, sparse_embed_lookup=False,
                 decoder=False):
        self.h = h  # hidden size
        self.l = l  # number of layers
        self.s = s  # sequence length
        self.v = v  # vocab size
        self.e = h if e is None else e  # embedding size
        self.i = h * 4 if i is None else i  # intermediate size
        self.kqv = h if head_size is None else head_size * heads  # attn proj sizes
        # attention heads
        self.heads = max(h // 64, 1) if heads is None else heads
        self.output_frac = output_frac  # percent of tokens using an output softmax
        self.sparse_embed_lookup = sparse_embed_lookup  # sparse embedding lookups
        self.decoder = decoder  # decoder has extra attn to encoder states
        
    def get_block_flops(self):
        """Get the forward-pass FLOPs for a single transformer block."""
        attn_mul = 2 if self.decoder else 1
        block_flops = dict(
            kqv=3 * 2 * self.h * self.kqv * attn_mul, # 6 h h
            kqv_bias=3 * self.kqv * attn_mul, # 3 h
            attention_scores=2 * self.kqv * self.s * attn_mul, # 2 h n
            attn_softmax=SOFTMAX_FLOPS * self.s * self.heads * attn_mul, # 5 n a
            attention_dropout=DROPOUT_FLOPS * self.s * self.heads * attn_mul, # 4 n a
            attention_scale=self.s * self.heads * attn_mul, # n a
            attention_weighted_avg_values=2 * self.kqv * self.s * attn_mul,  # self.h = self.heads * self.head_size # 2 h n
            attn_output=2 * self.kqv * self.h * attn_mul, # 2 h h
            attn_output_bias=self.h * attn_mul, # h
            attn_output_dropout=DROPOUT_FLOPS * self.h * attn_mul, # 4 h
            attn_output_residual=self.h * attn_mul, # h
            attn_output_layer_norm=LAYER_NORM_FLOPS * attn_mul, # 5
            
            intermediate=2 * self.h * self.i, # 2 h i
            intermediate_act=ACTIVATION_FLOPS * self.i, # 8 i
            intermediate_bias=self.i, # i
            output=2 * self.h * self.i, # 2 h i
            output_bias=self.h, # h
            output_dropout=DROPOUT_FLOPS * self.h, # 4 h
            output_residual=self.h, # h
            output_layer_norm=LAYER_NORM_FLOPS * self.h, # 5 h
        )
        return sum(block_flops.values()) * self.s

    def get_embedding_flops(self, output=False):
        """Get the forward-pass FLOPs the transformer inputs or output softmax."""
        embedding_flops = {}
        if output or (not self.sparse_embed_lookup):
            embedding_flops["main_multiply"] = 2 * self.e * self.v
        # input embedding post-processing
        if not output:
            embedding_flops.update(dict(
                tok_type_and_position=2 * self.e * (self.s + 2),
                add_tok_type_and_position=2 * self.e,
                emb_layer_norm=LAYER_NORM_FLOPS * self.e,
                emb_dropout=DROPOUT_FLOPS * self.e
            ))
        # projection layer if e != h
        if self.e != self.h or output:
            embedding_flops.update(dict(
                hidden_kernel=2 * self.h * self.e,
                hidden_bias=self.e if output else self.h
            ))
            # extra hidden layer and output softmax
            if output:
                embedding_flops.update(dict(
                    hidden_activation=ACTIVATION_FLOPS * self.e,
                    hidden_layernorm=LAYER_NORM_FLOPS * self.e,
                    output_softmax=SOFTMAX_FLOPS * self.v,
                    output_target_word=2 * self.v
                ))
                return self.output_frac * sum(embedding_flops.values()) * self.s
        return sum(embedding_flops.values()) * self.s

    def get_binary_classification_flops(self):
        classification_flops = dict(
            hidden=2 * self.h * self.h,
            hidden_bias=self.h,
            hidden_act=ACTIVATION_FLOPS * self.h,
            logits=2 * self.h
        )
        return sum(classification_flops.values()) * self.s

    def get_train_flops(self, batch_size, train_steps, discriminator=False):
        """Get the FLOPs for pre-training the transformer."""
        # 2* for forward/backward pass
        return 2 * batch_size * train_steps * (
            (self.l * self.get_block_flops()) +
            self.get_embedding_flops(output=False) +
            (self.get_binary_classification_flops() if discriminator else
             self.get_embedding_flops(output=True))
        )

    def get_infer_flops(self):
        """Get the FLOPs for running inference with the transformer on a
        classification task."""
        return ((self.l * self.get_block_flops()) +
                self.get_embedding_flops(output=False) +
                self.get_binary_classification_flops())

def get_electra_train_flops(
        h_d, l_d, h_g, l_g, batch_size, train_steps, tied_embeddings,
        e=None, s=512, output_frac=0.15625):
    """Get the FLOPs needed for  pre-training ELECTRA."""
    if e is None:
        e = h_d
    disc = TransformerHparams(
        h_d, l_d, s=s, e=e,
        output_frac=output_frac).get_train_flops(batch_size, train_steps, True)
    gen = TransformerHparams(
        h_g, l_g, s=s, e=e if tied_embeddings else None,
        output_frac=output_frac).get_train_flops(batch_size, train_steps)
    return disc + gen


MODEL_FLOPS = collections.OrderedDict([
    # These runtimes were computed with tensorflow FLOPs counting instead of the
    # script, as the neural architectures are quite different.
    # 768648884 words in LM1b benchmark, 10 epochs with batch size 20,
    # seq length 128, 568093262680 FLOPs per example.
    ("elmo", 2 * 10 * 768648884 * 568093262680 / (20.0 * 128)),
    # 15064773691518 is FLOPs for forward pass on 32 examples.
    # Therefore 2 * steps * batch_size * 15064773691518 / 32 is XLNet compute
    ("xlnet", 2 * 500000 * 8192 * 15064773691518 / 32.0),

    # Runtimes computed with the script
    ("gpt", TransformerHparams(768, 12, v=40000, output_frac=1.0).get_train_flops(
        128, 960800)),
    ("bert_small", TransformerHparams(
        256, 12, e=128, s=128).get_train_flops(128, 1.45e6)),
    ("bert_base", TransformerHparams(768, 12).get_train_flops(256, 1e6)),
    ("bert_large", TransformerHparams(1024, 24).get_train_flops(256, 1e6)),
    ("electra_small", get_electra_train_flops(
        256, 12, 64, 12, 128, 1e6, True, s=128, e=128)),
    ("electra_base", get_electra_train_flops(768, 12, 256, 12, 256, 766000, True)),
    ("electra_400k", get_electra_train_flops(
        1024, 24, 256, 24, 2048, 400000, True)),
    ("electra_1.75M", get_electra_train_flops(
        1024, 24, 256, 24, 2048, 1750000, True)),

    # RoBERTa, ALBERT, and T5 have  minor architectural differences from
    # BERT/ELECTRA, but I believe they don't significantly effect the runtime,
    # so we use this script for those models as well.
    ("roberta", TransformerHparams(1024, 24, v=50265).get_train_flops(8000, 500000)),
    ("albert", TransformerHparams(4096, 12, v=30000, e=128).get_train_flops(
        4096, 1.5e6)),
    ("t5_11b", TransformerHparams(
        1024,  # hidden size
        24,  # layers
        v=32000,  # vocab size
        i=65536,  # ff intermediate hidden size
        heads=128, head_size=128,  # heads/head size
        output_frac=0.0  # encoder has no output softmax
    ).get_train_flops(2048, 1e6) +  # 1M steps with batch size 2048
        TransformerHparams(
        1024,
        24,
        v=32000,
        i=65536,
        heads=128, head_size=128,
        output_frac=1.0,  # decoder has output softmax for all positions
        decoder=True
    ).get_train_flops(2048, 1e6))
])


class ViTHparams(TransformerHparams):
    def __init__(self, image_size=224, patch_size=16, channels=3, num_classes=1000, mlp_dim=None, **kwargs):
      self.image_size = image_size
      self.patch_size = patch_size
      self.num_patches = (image_size // patch_size) ** 2
      self.channels = channels
      self.num_classes = num_classes
      super().__init__(**kwargs, s=self.num_patches + 1)
      if mlp_dim is None:
        self.mlp_dim = self.h * 4
      else:
        self.mlp_dim = mlp_dim
      
    def get_embedding_flops(self):
      embedding_flops = {}
      embedding_flops.update(dict(
        patch_to_embedding = 2 * self.num_patches * self.channels * (self.patch_size ** 2) * self.e,
        add_position = (self.num_patches + 1) * self.e
      ))
      return sum(embedding_flops.values())

    def get_classification_flops(self):
        classification_flops = dict(
            internal_matmul = 2 * self.h * self.mlp_dim,
            internal_bias = self.mlp_dim,
            internal_act = ACTIVATION_FLOPS * self.mlp_dim,
            output_matmul = 2 * self.mlp_dim * self.num_classes,
            output_bias = self.num_classes,
            output_act = self.num_classes,
        )
        return sum(classification_flops.values())

    def get_infer_flops(self):
        return (self.get_embedding_flops() + 
              self.l * self.get_block_flops() + 
              self.get_classification_flops())


class PrunedViTHparams(ViTHparams):
    def __init__(self, num_heads_per_layer, ffn_sparsity_per_layer, **kwargs):
        intermediate_size = int((1 - ffn_sparsity_per_layer) * kwargs.pop('i', kwargs['h'] * 4))
        head_size = kwargs.pop('head_size', 64)
        super().__init__(heads=num_heads_per_layer, head_size=head_size, i=intermediate_size, **kwargs)

    @staticmethod
    def get_pruned_deit_flops(type, num_heads_per_layer, ffn_sparsity_per_layer):
        assert type in ['tiny', 'small', 'base']
        hidden_size_dict = {'tiny': 192, 'small': 384, 'base': 768}
        h = hidden_size_dict[type]
        return PrunedViTHparams(num_heads_per_layer=num_heads_per_layer,
                                ffn_sparsity_per_layer=ffn_sparsity_per_layer, 
                                h=h, l=12
        ).get_infer_flops()

    @staticmethod
    def experiment_show_pruned_deit_flops():
        type2heads_dict = {'tiny': 3, 'small': 6, 'base': 12}
        print('=== MACs of Pruned DeiT === (MMACs)')
        print('** 1) only prune ffn ***')
        for type in ['tiny', 'small', 'base']:
            flops_list = []
            num_heads = type2heads_dict[type]
            for sparsity in range(0, 100, 10):
                sparsity /= 100
                flops = PrunedViTHparams.get_pruned_deit_flops(type, num_heads_per_layer=num_heads, ffn_sparsity_per_layer=sparsity)
                flops_list.append(round(flops / 2e6, 2))
            print(type, flops_list)

        print('** 2) only prune head ***')
        for type in ['tiny', 'small', 'base']:
            flops_list = []
            num_heads = type2heads_dict[type]
            for heads in range(1, num_heads + 1):
                flops = PrunedViTHparams.get_pruned_deit_flops(type, num_heads_per_layer=heads, ffn_sparsity_per_layer=0)
                flops_list.append(round(flops / 2e6, 2))
            print(type, flops_list)

        print('** 3) prune head + ffn **')
        tiny_flops_list = []
        tiny_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('tiny', 2, 0.1) / 2e6, 2))
        tiny_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('tiny', 2, 0.2) / 2e6, 2))
        tiny_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('tiny', 2, 0.3) / 2e6, 2))
        print('tiny head2 ffn ', tiny_flops_list)

        small_flops_list = []
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 4, 0.1) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 4, 0.2) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 4, 0.3) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 4, 0.4) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 5, 0.1) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 5, 0.2) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 5, 0.3) / 2e6, 2))
        small_flops_list.append(round(PrunedViTHparams.get_pruned_deit_flops('small', 5, 0.4) / 2e6, 2))
        print('small head4', small_flops_list[:4])
        print('small head5', small_flops_list[4:])


class SwinFlops:
    def __init__(self, depths: List, base_dim: int,  mlp_ratio: float, base_heads: int, image_size=224, patch_size=4, window_size=7, num_stages=4, num_classes=1000) -> None:
        self.depth_list = depths
        self.hidden_size_list = [(1 << i) * base_dim for i in range(num_stages)]
        self.mlp_ratio = mlp_ratio
        self.heads_list = [(1 << i) * base_heads for i in range(num_stages)]
        self.seq_len_list = [(image_size // patch_size) ** 2 // (1 << i) ** 2 for i in range(num_stages)]
        self.window_size = window_size
        self.image_size = image_size
        self.patch_size = patch_size
        self.num_stages = num_stages
        self.num_classes = num_classes

    @staticmethod
    def block_flops(seq_len, hidden_size, mlp_ratio, num_heads, window_size) -> int:
        flops = 0
        flops += seq_len * hidden_size # norm1
        flops += SwinFlops.window_attention_flops(seq_len, hidden_size, num_heads, window_size)
        flops += seq_len * hidden_size # norm2
        flops += SwinFlops.mlp_flops(seq_len, hidden_size, mlp_ratio)
        return flops

    @staticmethod
    def window_attention_flops(seq_len, hidden_size, num_heads, window_size) -> int:
        flops = 0
        flops += 4 * seq_len * hidden_size * hidden_size # Linear Layer
        
        num_windows = seq_len // window_size ** 2
        seq_len_per_window = window_size ** 2
        head_size = hidden_size // num_heads
        flops_per_head = 0
        flops_per_head += 2 * seq_len_per_window ** 2 * head_size # QKV
        flops_per_head += 2 * seq_len_per_window ** 2 # softmax

        flops += num_windows * num_heads * flops_per_head # attention
        return flops

    @staticmethod
    def mlp_flops(seq_len, hidden_size, mlp_ratio) -> int:
        flops = 2 * seq_len * hidden_size * hidden_size * mlp_ratio
        return flops

    @staticmethod
    def patch_merging_flops(seq_len, hidden_size) -> int:
        flops = 0
        flops += seq_len * hidden_size # norm
        flops += (seq_len // 4) * (4 * hidden_size) * (2 * hidden_size)
        return flops

    @staticmethod
    def patch_embedding_flops(image_size, patch_size, hidden_size) -> int:
        seq_len = (image_size // patch_size) ** 2
        input_token_size = 3 * patch_size * patch_size
        return seq_len * input_token_size * hidden_size # ignore norm

    @staticmethod
    def classification_head_flops(seq_len, hidden_size, num_classes) -> int:
        flops = 0
        flops += 2 * seq_len * hidden_size # pooling + norm
        flops += hidden_size * num_classes # Linear
        return flops

    def get_flops(self) ->int:
        flops = 0
        flops += SwinFlops.patch_embedding_flops(image_size=self.image_size, patch_size=self.patch_size, hidden_size=self.hidden_size_list[0])
        for i in range(self.num_stages):
            depth = self.depth_list[i]
            hidden_size = self.hidden_size_list[i]
            seq_len = self.seq_len_list[i]
            num_heads = self.heads_list[i]
            flops += depth * SwinFlops.block_flops(seq_len=seq_len, hidden_size=hidden_size, mlp_ratio=self.mlp_ratio, num_heads=num_heads, window_size=self.window_size)
            flops += SwinFlops.patch_merging_flops(seq_len=seq_len, hidden_size=hidden_size)
        flops += SwinFlops.classification_head_flops(seq_len=self.seq_len_list[-1], hidden_size=self.hidden_size_list[-1], num_classes=self.num_classes)
        return flops 

MY_FLOPS = dict(
    bert_small = TransformerHparams(l = 4, h = 512, s = 128).get_block_flops() * 4, 
    bert_medium = TransformerHparams(l = 8, h = 512, s = 128).get_block_flops() * 8,
    bert_base = TransformerHparams(l = 12, h = 768, s = 128).get_block_flops() * 12,
    bert_base_196 = TransformerHparams(l = 12, h = 768, s = 196).get_block_flops() * 12,
    vit = ViTHparams(image_size=224).get_infer_flops(),
    vit_base_384 = ViTHparams(image_size=384).get_infer_flops(),
    deit_base = ViTHparams(l=12, h=768, image_size=224).get_infer_flops(),
    deit_small = ViTHparams(l=12, h=384, image_size=224).get_infer_flops(),
    deit_tiny = ViTHparams(l=12, h=192, image_size=224).get_infer_flops()
)



def main():
    PrunedViTHparams.experiment_show_pruned_deit_flops()
    # flops = SwinFlops(depths=[2, 2, 18, 2], base_dim=128, mlp_ratio=4, base_heads=3).get_flops()
    # print(flops / 1e9)

if __name__ == "__main__":
    main()