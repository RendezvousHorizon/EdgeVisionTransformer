#!/bin/bash

TASK=$1
OPTIONS="${@:2}"

# expected options: 
# 1. Eval only: --deit_type base|small|tiny  --prune_number `seq 0 132|60|24` --head_importance_file xxx
# 2. Retrain: --deit_type base|small|tiny  --prune_number `seq 0 132|60|24` --head_importance_file xxx \
#    --train_batch_size 100 --n_retrain_epochs_after_pruning  12800 * n_epochs --retrain_learning_rate 1e-3
function run_eval () {
    python /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run_classifier.py \
    --task_name "ImageNet1K" \
    --do_eval \
    --normalize_pruning_by_layer \
    --do_prune \
    --eval_pruned \
    --actually_prune \
    --data_dir /data/data1/v-xudongwang/imagenet \
    --eval_batch_size 500 \
    --at_least_x_heads_per_layer 1 \
    --output_dir /data/data1/v-xudongwang/models/torch_model 2>&1 \
    $OPTIONS
}

function distributed_launch() {
    python -m torch.distributed.launch --nproc_per_node 4 /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run_classifier.py \
    --task_name "ImageNet1K" \
    --normalize_pruning_by_layer \
    --do_prune \
    --eval_pruned \
    --actually_prune \
    --data_dir /data/data1/v-xudongwang/imagenet \
    --eval_batch_size 500 \
    --at_least_x_heads_per_layer 1 \
    --num_workers 8 \
    --use_huggingface_trainer \
    $OPTIONS
}

function distributed_launch_another_port() {
    python -m torch.distributed.launch --nproc_per_node 2 --master_port 12345 /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run_classifier.py \
    --task_name "ImageNet1K" \
    --normalize_pruning_by_layer \
    --do_prune \
    --eval_pruned \
    --actually_prune \
    --data_dir /data/data1/v-xudongwang/imagenet \
    --eval_batch_size 500 \
    --at_least_x_heads_per_layer 1 \
    --num_workers 8 \
    --use_huggingface_trainer \
    $OPTIONS
}

function distributed_launch_no_eval() {
    python -m torch.distributed.launch --nproc_per_node 4 /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run_classifier.py \
    --task_name "ImageNet1K" \
    --normalize_pruning_by_layer \
    --do_prune \
    --actually_prune \
    --data_dir /data/data1/v-xudongwang/imagenet \
    --eval_batch_size 500 \
    --at_least_x_heads_per_layer 1 \
    --num_workers 8 \
    $OPTIONS
}

function export_onnx_pruned() {
    python /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run_classifier.py \
    --task_name "ImageNet1K" \
    --normalize_pruning_by_layer \
    --do_prune \
    --actually_prune \
    --data_dir /data/data1/v-xudongwang/imagenet \
    --at_least_x_heads_per_layer 1 \
    --export_onnx \
    --output_dir /data/data1/v-xudongwang/models/torch_model \
    --onnx_output_dir /data/data1/v-xudongwang/models/onnx_model \
    $OPTIONS # --deit_type base --prune_number `seq 0 132` --head_importance_file xxx
}

function iterative_pruning() {
    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch \
    --deit_type tiny \
    --prune_number `seq 0 24` \
    --exact_pruning \
    --train_batch_size 256 \
    --n_retrain_epochs_after_pruning 3 \
    --retrain_learning_rate 0.0001 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.0001 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/iterative/tiny \


    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch \
    --deit_type small \
    --prune_number `seq 0 2 60` \
    --exact_pruning \
    --train_batch_size 128 \
    --n_retrain_epochs_after_pruning 3 \
    --retrain_learning_rate 0.00005 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.00005 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/iterative/small

    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch \
    --deit_type base \
    --prune_number `seq 0 4 132` \
    --exact_pruning \
    --train_batch_size 64 \
    --n_retrain_epochs_after_pruning 3 \
    --retrain_learning_rate 0.000025 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.000025 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/iterative/base
}


function no_iterative_pruning() {
    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch_another_port \
    --deit_type tiny \
    --prune_number `seq 0 24` \
    --head_importance_file /data/data1/v-xudongwang/benchmark_tools/are_16_heads/deit_tiny_head_importance.txt \
    --train_batch_size 256 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.00005 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/no_iterative/tiny

    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch_another_port \
    --deit_type small \
    --prune_number `seq 0 2 60` \
    --head_importance_file /data/data1/v-xudongwang/benchmark_tools/are_16_heads/deit_small_head_importance.txt \
    --train_batch_size 128 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.0000025 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/no_iterative/small

    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch_another_port \
    --deit_type base \
    --prune_number `seq 0 4 132` \
    --head_importance_file /data/data1/v-xudongwang/benchmark_tools/are_16_heads/deit_base_head_importance.txt \
    --train_batch_size 64 \
    --n_finetune_epochs_after_pruning 3 \
    --finetune_learning_rate 0.0000125 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/no_iterative/base
}

function valid_head_importance_calc() {
    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch_no_eval \
    --deit_type tiny \
    --prune_number `seq 8 8 24` \
    --head_importance_file /data/data1/v-xudongwang/benchmark_tools/are_16_heads/deit_tiny_head_importance.txt \
    --exact_pruning \
    --train_batch_size 256 \
    --output_dir /data/data1/v-xudongwang/models/torch_model/
}

function debug() {
    # /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch \
    # --deit_type tiny \
    # --prune_number `seq 3 3 24` \
    # --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/debug_iterative \
    # --compute_head_importance_on_subset 0.001 \
    # --head_importance_file /data/data1/v-xudongwang/benchmark_tools/are_16_heads/deit_tiny_head_importance.txt \
    # --train_batch_size 256 \
    # --n_retrain_epochs_after_pruning 1 \
    # --retrain_learning_rate 0.0000025  \
    /data/data1/v-xudongwang/benchmark_tools/are_16_heads/run.sh distributed_launch_another_port \
    --deit_type tiny \
    --prune_number `seq 3 3 24` \
    --exact_pruning \
    --compute_head_importance_on_subset 0.001 \
    --train_batch_size 256 \
    --n_retrain_steps_after_pruning 30 \
    --retrain_learning_rate 0.0001 \
    --n_finetune_steps_after_pruning  30\
    --finetune_learning_rate 0.0001 \
    --eval_finetuned \
    --output_dir /data/data1/v-xudongwang/models/torch_model/are_16_heads/debug_iterative \
    # --n_finetune_epochs_after_pruning 3 \
    # --finetune_learning_rate 0.00005 \
    # --eval_finetuned \
}
$TASK ""
