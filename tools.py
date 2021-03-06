import sys
import argparse
import timeit
import numpy as np


def server_benchmark():
    import onnx
    import onnxruntime as ort
    import numpy as np
    import os
    from utils import get_onnx_model_inputs


    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help="onnx model path")
    
    parser.add_argument('--use_gpu', required=False, action='store_true', help="use GPU")
    parser.set_defaults(use_gpu=False)

    parser.add_argument('--num_runs',
                        required=False,
                        type=int,
                        default=50,
                        help="number of times to run per sample. By default, the value is 1000 / samples")
    parser.add_argument(
        '--warmup_runs',
        required=False,
        type=int,
        default=50,
    )
    parser.add_argument(
        '--dtype',
        default='float32',
        type=str,
        help='input data type'
    )
    parser.add_argument(
        '--intra_op_threads',
        type=int,
        default=1,
    )
    parser.add_argument(
        '--top',
        type=int,
        default=None,
        help='number of shortest runs to take average'
    )
    parser.add_argument(
        '--io_binding',
        action='store_true',
        dest='io_binding'
    )
    parser.add_argument(
        '--precision',
        default=2,
        choices=[2,3,4,5,6],
        type=int,
    )
    parser.add_argument(
        '--input_shape',
        default=None,
        type=str,
        help='input_shape'
    )
    parser.set_defaults(io_binding=False)
    args = parser.parse_args()

    execution_providers = ['CPUExecutionProvider'
                               ] if not args.use_gpu else ['CUDAExecutionProvider', 'CPUExecutionProvider']
    session_options = ort.SessionOptions()
    session_options.intra_op_num_threads = args.intra_op_threads
    session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(args.model, providers=execution_providers, sess_options=session_options)
    if args.io_binding:
        io_binding = session.io_binding()
    model = onnx.load(args.model)

    # warm up
    for _ in range(args.warmup_runs):
        input = get_onnx_model_inputs(model, args.dtype)
        if args.io_binding:
            io_binding.bind_cpu_input('input', input['input'])
            io_binding.bind_output('output')
            session.run_with_iobinding(io_binding)
        else:
            session.run(None, input)

    # run
    latency_list = []
    for _ in range(args.num_runs):
        input = get_onnx_model_inputs(model, args.dtype, [int(x) for x in args.input_shape.split(',')] if args.input_shape else None)
        if args.io_binding:
            io_binding.bind_cpu_input('input', input['input'])
            io_binding.bind_output('output')

        start_time = timeit.default_timer()

        if args.io_binding:
            session.run_with_iobinding(io_binding)
        else:
            session.run(None, input)
            
        latency = timeit.default_timer() - start_time
        latency_list.append(latency)

    # summarize
    latency_list = sorted(latency_list)
    if args.top:
        latency_list = latency_list[:args.top]
    avg_latency = np.average(latency_list)
    std_latency = np.std(latency_list)
    print(f'{os.path.basename(args.model)}  Avg latency: {avg_latency * 1000: .{args.precision}f} ms, Std: {std_latency * 1000: .{args.precision}f} ms.')


def test_tf_latency():
    import tensorflow as tf
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help="SavedModel path")
    
    parser.add_argument('--use_gpu', required=False, action='store_true', help="use GPU")
    parser.set_defaults(use_gpu=False)

    parser.add_argument('--test_times',
                        required=False,
                        type=int,
                        default=5,
                        help="number of times to run per sample. By default, the value is 1000 / samples")
    parser.add_argument('--input_shape', required=True, type=str, help='input shape')

    args = parser.parse_args()

    input_shape = [int(num) for num in args.input_shape.split(',')]


    latency_list = []
    for _ in range(args.test_times):
        input_tensor = tf.random.uniform(input_shape)
        
        graph = tf.Graph()
        with graph.as_default():
            with tf.Session() as sess:
                tf.saved_model.loader.load(
                    sess,
                    [tf.saved_model.tag_constants.SERVING],
                    args.model,
                )
                # output_placeholder = graph.get_tensor_by_name('StatefulPartitionedCall:0')
                output_placeholder = graph.get_tensor_by_name('PartitionedCall:0')
                
                input_placeholder = graph.get_tensor_by_name('serving_default_input:0')

                start_time = timeit.default_timer()

                sess.run(output_placeholder, feed_dict={
                    input_placeholder: np.random.uniform(0, 1, [1,3,224,224])
                })

                latency = timeit.default_timer() - start_time
                latency_list.append(latency)

    avg_latency = np.average(latency_list[1:])
    print(f'Avg latency: {avg_latency * 1000: .2f}ms')


def test_keras_latency():
    import tensorflow as tf
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help="keras SavedModel path")
    
    parser.add_argument('--use_gpu', required=False, action='store_true', help="use GPU")
    parser.set_defaults(use_gpu=False)

    parser.add_argument('--test_times',
                        required=False,
                        type=int,
                        default=5,
                        help="number of times to run per sample. By default, the value is 1000 / samples")
    parser.add_argument('--input_shape', required=True, type=str, help='input shape')

    args = parser.parse_args()

    input_shape = [int(num) for num in args.input_shape.split(',')]


    model = tf.keras.models.load_model(args.model)
    print(f'Successfully loaded model from {args.model}.')

    latency_list = []
    for _ in range(args.test_times + 1):
        # input_tensor = np.random.uniform(0, 1, input_shape)
        inputs = None
        if isinstance(model.input, dict):
            inputs = {}
            for k, v in model.input.items():
               inputs[k] = tf.ones(shape=v.shape, dtype=v.dtype)
        else:
            inputs = tf.random.normal(input_shape)
        start_time = timeit.default_timer()

        _ = model(inputs)

        latency = timeit.default_timer() - start_time
        latency_list.append(latency)

    avg_latency = np.average(latency_list[1:])
    print(f'Avg latency: {avg_latency * 1000: .2f}ms')


def export_onnx_cmd():
    import torch
    from utils import export_onnx
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help="pytorch model path")
    parser.add_argument('--output', required=True, type=str, help='onnx output path')
    parser.add_argument('--input_shape', required=True, type=str, help='input shape')
    parser.add_argument('--opset_version', required=False, type=int, default=12, help='opset version')

    args = parser.parse_args()

    torch_model_path = args.model 
    onnx_model_path = args.output
    input_shape = [int(num) for num in args.input_shape.split(',')]
    opset_version = args.opset_version
    
    model = torch.load(torch_model_path)
    if isinstance(model, dict):
        if 'model' in model.keys():
            model = model['model']
        else:
            print('please specify the key to load model.')
            exit(-1)
    # model = torch.hub.load('facebookresearch/deit:main', 'deit_base_patch16_224', pretrained=True)
    export_onnx(model, onnx_model_path, input_shape, opset_version)


def export_onnx_deit():
    import torch
    from utils import export_onnx, get_torch_deit
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--output', required=True, type=str, help='onnx output path')
    parser.add_argument('--input_shape', required=True, type=str, help='input shape')
    parser.add_argument('--type', type=str, choices=['tiny', 'small', 'base'], default='base', help='deit config')
    parser.add_argument('--fix_batch', action='store_true', dest='fix_batch')
    parser.set_defaults(fix_batch=False)
    args = parser.parse_args()

    onnx_model_path = args.output
    input_shape = [int(num) for num in args.input_shape.split(',')]
    type = args.type
    fix_batch = args.fix_batch


    model = get_torch_deit(type)
    export_onnx(model, onnx_model_path, input_shape, dynamic_batch=not fix_batch)


def export_onnx_swin():
    import torch
    from utils import export_onnx, get_swin
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--output', required=True, type=str, help='onnx output path')
    parser.add_argument('--input_shape', default='1,3,224,224', type=str, help='input shape')
    parser.add_argument('--type', type=str, choices=['tiny', 'small', 'base'], default='base', help='swin config')
    parser.add_argument('--swin_repo_root_path', default='/data/v-xudongwang/Swin-Transformer', type=str, help='Swin-Transformer github repo root path')
    parser.add_argument('--pretrained_path', default=None, type=str, help='pretrained state_dict path')
    parser.add_argument('--fix_batch', action='store_true', dest='fix_batch')
    parser.set_defaults(fix_batch=False)
    args = parser.parse_args()

    onnx_model_path = args.output
    input_shape = [int(num) for num in args.input_shape.split(',')]
    config_file_name = f'swin_{args.type}_patch4_window7_224'
    fix_batch = args.fix_batch
    pretrained_path = args.pretrained_path

    model = get_swin(config_file_name, args.swin_repo_root_path)
    if pretrained_path:
        state_dict = torch.load(pretrained_path, map_location='cpu')
        model.load_state_dict(state_dict['model'])
        print(f'Load state_dict from {pretrained_path}')

    export_onnx(model, onnx_model_path, input_shape, dynamic_batch=not fix_batch)

def export_onnx_bert_huggingface():
    import torch
    from utils import get_huggingface_bert

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--layer', required=True, type=int, help='number of layers')
    parser.add_argument('--hidden', required=True, type=int, help='hidden size')
    parser.add_argument('--seq_len', required=False, default=128, type=int, help='sequence length')
    parser.add_argument('--output', required=True, type=str, help='output path')
    args = parser.parse_args()

    model = get_huggingface_bert(l=args.layer, h=args.hidden)
    seq_len = args.seq_len
    output_path = args.output

    inputs = {
        'input_ids': torch.randint(low=0, high=10000, size=[1, seq_len], dtype=torch.int64),
        'token_type_ids': torch.zeros(size=[1, seq_len], dtype=torch.int64),
        'attention_mask': torch.ones(size=[1, seq_len], dtype=torch.int64)
    }

    torch.onnx.export(
        model,
        tuple(inputs.values()),
        output_path,
        input_names=list(inputs.keys()),
        output_names=['last_hidden_state', 'pooler_output'],
        verbose=False,
        export_params=True,
        opset_version=12,
        do_constant_folding=True,
        dynamic_axes={
            'input_ids': {0: 'batch_size'},
            'token_type_ids': {0: 'batch_size'},
            'attention_mask': {0: 'batch_size'},
            'last_hidden_state': {0: 'batch_size'},
            'pooler_output': {0: 'batch_size'},
        }
    )

    print(f'Successfully export bert huggingface model to {output_path}.')



def export_onnx_t2t_vit():
    from utils import export_onnx, import_from_path


    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--output', '-o', default=None, type=str, help='output path')
    parser.add_argument('--version', '-v', type=int, choices=[7, 10, 12, 14], required=True, help='T2T-ViT version')
    parser.add_argument('--t2t_vit_dir', default='other_codes/t2t_vit', type=str, help='T2T-ViT-main repo path')
    parser.add_argument('--pretrained', default=False, type=bool, help='whether load weights')
    parser.add_argument('--weight_path', '-w', default=None, type=str, help='torch state_dict path')
    parser.add_argument('--input_shape', default='1,3,224,224', type=str, help='input image shape')
    args = parser.parse_args()

    if args.output is None:
        args.output = f'models/onnx_model/t2t_vit_{args.version}.onnx'
    import os, sys
    sys.path.insert(1, args.t2t_vit_dir)

    from models import t2t_vit_7, t2t_vit_10, t2t_vit_12, t2t_vit_14

    weight_dict = {
        7: 'models/torch_model/71.7_T2T_ViT_7.pth.tar',
        10: 'models/torch_model/75.2_T2T_ViT_10.pth.tar',
        12: 'models/torch_model/76.5_T2T_ViT_12.pth.tar',
        14: 'models/torch_model/81.5_T2T_ViT_14.pth.tar',
    }
    get_model_dict = {
        7: t2t_vit_7,
        10: t2t_vit_10,
        12: t2t_vit_12,
        14: t2t_vit_14,
    }
    if args.weight_path is None:
        args.weight_path = weight_dict[args.version]

    model = get_model_dict[args.version]()

    if args.pretrained:
        import torch
        state_dict = torch.load(args.weight_path)
        state_dict = state_dict['state_dict_ema']
        model.load_state_dict(state_dict)

    input_shape = [int(x) for x in args.input_shape.split(',')]
    export_onnx(model, args.output, input_shape)



def export_onnx_distilbert_huggingface():
    import torch

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--seq_len', required=False, default=128, type=int, help='sequence length')
    parser.add_argument('--output', required=True, type=str, help='output path')
    args = parser.parse_args()

    from transformers import DistilBertModel, DistilBertConfig
    config = DistilBertConfig()
    model = DistilBertModel(config)


    seq_len = args.seq_len
    output_path = args.output

    inputs = {
        'input_ids': torch.randint(low=0, high=10000, size=[1, seq_len], dtype=torch.int64),
        'attention_mask': torch.ones(size=[1, seq_len], dtype=torch.int64)
    }

    torch.onnx.export(
        model,
        tuple(inputs.values()),
        output_path,
        input_names=list(inputs.keys()),
        output_names=['last_hidden_state'],
        verbose=False,
        export_params=True,
        opset_version=12,
        do_constant_folding=True,
        dynamic_axes={
            'input_ids': {0: 'batch_size'},
            'attention_mask': {0: 'batch_size'},
            'last_hidden_state': {0: 'batch_size'},
        }
    )

    print(f'Successfully export distilBERT huggingface model to {output_path}.')


def save_bert_encoder():
    from utils import get_bert_encoder
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('-l', required=True, type=int, help='number of layers')
    parser.add_argument('--hidden', required=True, type=int, help='hidden size')
    parser.add_argument('--seq_len', required=True, type=int, help='sequence length')
    parser.add_argument('--output', required=True, type=str, help='output path')
    args = parser.parse_args()

    model = get_bert_encoder(num_layers=args.l, hidden_size=args.hidden, num_heads=args.hidden//64, seq_len=args.seq_len)

    model.save(args.output)
    print(f'save model to {args.output}')


def export_onnx_proxyless_mobile():
    from utils import export_onnx
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    args = parser.parse_args()

    # export PYTHONPATH=/data/v-xudongwang/other_codes/proxylessnas-master
    from proxyless_nas import proxyless_mobile
    model = proxyless_mobile(pretrained=False)
    export_onnx(model, 'models/onnx_model/proxyless_mobile.onnx', [1,3,224,224])


def tf2tflite_cmd():
    import tensorflow as tf
    from utils import tf2tflite
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--input',  required=True, type=str, help='input path')
    parser.add_argument('--output', required=True, type=str, help='output path')
    parser.add_argument('--quantization', default='None', choices=['None', 'dynamic', 'float16', 'int8'], type=str, help='quantization type')
    parser.set_defaults(keras=False)
    parser.add_argument('--no_flex', action='store_false', dest='use_flex', help='specify not to use flex op')
    parser.add_argument('--input_shape', type=str, default=None, help='input_shape to generate fake dataset when perform int8 quantization')
    parser.set_defaults(use_flex=True)
    args = parser.parse_args()

    if args.quantization == 'int8' and args.input_shape is None:
        raise ValueError('--input_shape must be specified when performing int8 quantization.')

    input_shape=None
    if args.input_shape:
        input_shape = [int(x) for x in args.input_shape.split(',')]

    tf2tflite(args.input, args.output, quantization=args.quantization, use_flex=args.use_flex, input_shape=input_shape)


def tf2tflite_dir_cmd():
    from utils import tf2tflite_dir
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--input_dir',  required=True, type=str, help='input path')
    parser.add_argument('--output_dir', required=True, type=str, help='output path')
    parser.add_argument('--quantization', default='None', choices=['None', 'dynamic', 'float16', 'int8'], type=str, help='quantization type')
    parser.add_argument('--skip_existed', action='store_true', help='skip if the output tflite file exists')
    parser.add_argument('--input_shape', type=str, default=None, help='input_shape to generate fake dataset when perform int8 quantization')
    args = parser.parse_args()

    input_shape=None
    if args.input_shape:
        input_shape = [int(x) for x in args.input_shape.split(',')]
    
    tf2tflite_dir(args.input_dir, args.output_dir, quantization=args.quantization, skip_existed=args.skip_existed, input_shape=input_shape)



def mobile_benchmark():
    from benchmark.ADBConnect import ADBConnect
    from benchmark.run_on_device import run_on_android

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='tflitemodel path')
    parser.add_argument('--use_gpu', dest='use_gpu', action='store_true')
    parser.add_argument('--num_runs', type=int, default=10, help='number of runs')
    parser.add_argument('--warmup_runs', type=int, default=10)
    parser.add_argument('--num_threads', type=int, default=1, help='number of threads')
    parser.add_argument('--taskset_mask', type=str, default='70', help='mask of taskset to set cpu affinity')
    parser.add_argument('--serial_number', type=str, default='98281FFAZ009SV', help='phone serial number in `adb devices`')
    parser.add_argument('--benchmark_binary_dir', type=str, default='/data/local/tmp', help='directory of binary benchmark_model_plus_flex')
    parser.add_argument('--bin_name', default='benchmark_model_plus_flex_r27', type=str, help='benchmark binary name')
    parser.add_argument('--skip_push', action='store_true', dest='skip_push')
    parser.add_argument('--no_root', action='store_true', help='run cmd on phone without root')
    parser.add_argument('--use_xnnpack', default='store_true', dest='use_xnnpack', help='use xnnpack delegate, default false')
    parser.add_argument('--profiling_output_csv_file', default=None, type=str, help='do profiling and save output to this path')
    parser.set_defaults(use_gpu=False)
    parser.set_defaults(skip_push=False)
    parser.set_defaults(use_xnnpack=False)
    args = parser.parse_args()

    model_path = args.model
    use_gpu = args.use_gpu
    num_threads = args.num_threads
    num_runs = args.num_runs
    warmup_runs = args.warmup_runs
    skip_push = args.skip_push
    mask = args.taskset_mask
    serial_number = args.serial_number
    benchmark_binary_directory = args.benchmark_binary_dir
    bin_name=args.bin_name
    no_root = args.no_root
    use_xnnpack = args.use_xnnpack
    profiling_output_csv_file = args.profiling_output_csv_file

    # patch for path related bugs on windows to linux
    if 'local/tmp' in benchmark_binary_directory:
        benchmark_binary_directory = '/data/local/tmp'
    if 'tf_benchmark' in benchmark_binary_directory:
        benchmark_binary_directory = '/data/tf_benchmark'

    adb = ADBConnect(serial_number)
    std_ms, avg_ms, mem_mb = run_on_android(model_path, adb, num_threads=num_threads, num_runs=num_runs, warmup_runs=warmup_runs, 
                                            benchmark_binary_dir=benchmark_binary_directory, bin_name=bin_name, taskset_mask=mask, use_gpu=use_gpu, 
                                            skip_push=skip_push, no_root=no_root, use_xnnpack=use_xnnpack, 
                                            profiling_output_csv_file=profiling_output_csv_file)
    print(std_ms / avg_ms * 100, f'Avg latency {avg_ms} ms,', f'Std {std_ms} ms. Mem footprint(MB): {mem_mb}')


def get_onnx_opset_version_cmd():
    from utils import get_onnx_opset_version

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='tflitemodel path')
    args = parser.parse_args()

    onnx_model_path = args.model 
    
    opset_version = get_onnx_opset_version(onnx_model_path)
    print(opset_version)


def onnx2tflite_cmd():
    from utils import onnx2tflite
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='onnx model path')
    parser.add_argument('--output', '-o', default=None, type=str, help='output tflite model path')
    parser.add_argument('--model_home', default=None, type=str, help='root dir of models')
    parser.add_argument('--save_tf', action='store_true', dest='save_tf', help='to save tf SavedModel')
    parser.set_defaults(save_tf=False)
    args = parser.parse_args()

    onnx_model_path = args.model
    output_path = args.output
    save_tf = args.save_tf
    model_home = args.model_home
    onnx2tflite(onnx_model_path, output_path, save_tf, model_home=model_home)


def save_vit():
    # first you need to : export PYTHONPATH=/data/v-xudongwang/other_codes/Vision-Transformer-main/
    from model import ViT
    import tensorflow as tf
    patch_size_list = [4, 8, 14, 16, 28, 32, 56]
    for patch_size in patch_size_list:
        vit_config = {"image_size":224,
                    "patch_size":patch_size,
                    "num_classes":1000,
                    "dim":768,
                    "depth":12,
                    "heads":12,
                    "mlp_dim":3072}

        vit = ViT(**vit_config)
        vit = tf.keras.Sequential([
            tf.keras.layers.InputLayer(input_shape=(3, vit_config["image_size"], vit_config["image_size"]), batch_size=1),
            vit,
        ])
        output_path = f'/data/v-xudongwang/models/tf_model/vit_patch{patch_size}_224.tf'
        vit.save(f'/data/v-xudongwang/models/tf_model/vit_patch{patch_size}_224.tf')
        print(f'Successfully save model to {output_path}.')


def get_flops_cmd():
    import tensorflow as tf
    from utils import get_flops
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='keras model path')
    args = parser.parse_args()

    model_path = args.model 
    model = tf.keras.models.load_model(model_path)
    print('Flops: ', get_flops(model))


def export_onnx_mobilenet():
    import timm
    import torch
    from utils import export_onnx
    mobilenetv2 = timm.create_model('mobilenetv2_100', pretrained=True)
    export_onnx(mobilenetv2, 'models/onnx_model/mobilenetv2.onnx', input_shape=[1,3,224,224])
    mobilenetv3_large = timm.create_model('mobilenetv3_large_100', pretrained=True)
    export_onnx(mobilenetv3_large, 'models/onnx_model/mobilenetv3_large.onnx', input_shape=[1,3,224,224])


def export_onnx_vit_huggingface():
    from utils import get_huggingface_vit_model, export_onnx

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--image_size', default=224, type=int, help='image_size')
    parser.add_argument('--patch_size', default=16, type=int, help='patch_size')
    parser.add_argument('--output', default=None, type=str, help='output onnx model path')
    args = parser.parse_args()

    image_size = args.image_size
    patch_size = args.patch_size
    output_path = args.output_path
    if output_path is None:
        output_path = f'models/onnx_model/vit_huggingface_patch{patch_size}_{image_size}.onnx'
    model = get_huggingface_vit_model(patch_size=patch_size, image_size=image_size)
    input_shape = [1, 3, image_size, image_size]
    export_onnx(model, output_path, input_shape)


def export_tflite_attention():
    from utils import tf2tflite, get_attention_plus_input

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--hidden_size', default=768, type=int)
    parser.add_argument('--num_heads', default=12, type=int)
    parser.add_argument('--head_size', default=None, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--tf_path', default=None, type=str, help='tf savedModel path')
    parser.add_argument('--output', '-o', default=None, type=str, help='output tflite model path')
    args = parser.parse_args()

    h = args.hidden_size
    a = args.num_heads
    n = args.seq_len
    h_k = args.head_size
    tf_path = args.tf_path
    output_path = args.output
    if output_path is None:
        if h_k is None:
            output_path = f'models/tflite_model/attention_h{h}_a{a}_n{n}.tflite'
        else:
            output_path = f'models/tflite_model/attention_h{h}_a{a}_hk{h_k}_n{n}.tflite'

    attn = get_attention_plus_input(h, a, h_k, n)
    if tf_path:
        attn.save(tf_path)
    tf2tflite(attn, output_path, is_keras_model=True)


def export_tflite_ffn():
    from utils import tf2tflite, get_ffn_plus_input

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--hidden_size', default=768, type=int)
    parser.add_argument('--intermediate_size', '-i', default=3072, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--tf_path', default=None, type=str, help='tf savedModel path')
    parser.add_argument('--output', '-o', default=None, type=str, help='output tflite model path')
    parser.add_argument('--only_ffn', action='store_true', dest='only_ffn', help='export ffn without residual and layernorm')
    parser.set_defaults(only_ffn=False)
    args = parser.parse_args()

    h = args.hidden_size
    i = args.intermediate_size
    n = args.seq_len
    tf_path = args.tf_path
    output_path = args.output
    only_ffn = args.only_ffn
    if output_path is None:
        output_path = f'models/tflite_model/ffn_h{h}_i{i}_n{n}.tflite'
    
    ffn = get_ffn_plus_input(h, i, n, only_ffn=only_ffn)
    if tf_path:
        ffn.save(tf_path)
    tf2tflite(ffn, output_path, is_keras_model=True)


def export_pb_ffn():
    from utils import get_ffn_tf1, save_to_pb
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--hidden_size', default=768, type=int)
    parser.add_argument('--intermediate_size', '-i', default=3072, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--output', '-o', default=None, type=str, help='output pb model path')
    parser.add_argument('--only_ffn', action='store_true', dest='only_ffn', help='export ffn without residual and layernorm')
    parser.set_defaults(only_ffn=False)
    args = parser.parse_args()

    h = args.hidden_size
    i = args.intermediate_size
    n = args.seq_len
    output_path = args.output
    only_ffn = args.only_ffn
    if output_path is None:
        output_path = f'models/pb_model/ffn_h{h}_i{i}_n{n}.pb'
    input, output = get_ffn_tf1(h, i, n, only_ffn=only_ffn)
    save_to_pb(outputs=[output], output_path=output_path)


def export_onnx_attention():
    from utils import get_attention_plus_input, export_onnx
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--hidden_size', default=768, type=int)
    parser.add_argument('--num_heads', default=12, type=int)
    parser.add_argument('--head_size', default=None, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--output', '-o', default=None, type=str, help='output onnx model path')
    args = parser.parse_args()

    h = args.hidden_size
    a = args.num_heads
    n = args.seq_len
    h_k = args.head_size
    output_path = args.output
    if output_path is None:
        if h_k is None:
            output_path = f'models/onnx_model/attention_h{h}_a{a}_n{n}.onnx'
        else:
            output_path = f'models/onnx_model/attention_h{h}_a{a}_hk{h_k}_n{n}.onnx'

    model = get_attention_plus_input(h=h, a=a, h_k=h_k, n=n, is_tf=False)
    export_onnx(model, output_path, input_shape=[1, n, h])


def export_onnx_ffn():
    from utils import export_onnx, get_ffn_plus_input

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--hidden_size', default=768, type=int)
    parser.add_argument('--intermediate_size', '-i', default=3072, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--output', '-o', default=None, type=str, help='output onnx model path')
    parser.add_argument('--only_ffn', action='store_true', dest='only_ffn', help='export ffn without residual and layernorm')
    parser.set_defaults(only_ffn=False)
    args = parser.parse_args()

    h = args.hidden_size
    i = args.intermediate_size
    n = args.seq_len
    output_path = args.output
    only_ffn = args.only_ffn
    if output_path is None:
        if not only_ffn:
            output_path = f'models/onnx_model/ffn_h{h}_i{i}_n{n}.onnx'
        else:
            output_path = f'models/onnx_model/ffn_h{h}_i{i}_n{n}_pure.onnx'

    
    model = get_ffn_plus_input(h, i, n, is_tf=False, only_ffn=only_ffn)
    export_onnx(model, output_path, input_shape=[1, n, h])


def export_onnx_dense():
    from utils import export_onnx, get_dense_plus_input

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--input_size', default=768, type=int)
    parser.add_argument('--output_size', default=3072, type=int)
    parser.add_argument('--seq_len', default=128, type=int)
    parser.add_argument('--output', '-o', default=None, type=str, help='output onnx model path')
    args = parser.parse_args()

    input_size = args.input_size
    output_size = args.output_size
    n = args.seq_len
    output_path = args.output
    if output_path is None:
        output_path = f'models/onnx_model/dense_i{input_size}_o{output_size}_n{n}.onnx'
    
    model = get_dense_plus_input(input_size, output_size, n=n, is_tf=False)
    export_onnx(model, output_path, input_shape=[1, n, input_size], dynamic_batch=False)


def fetch_latency_std_cmd():
    from utils import fetch_latency_std

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--file', '-f', required=True, type=str, help='log file')
    parser.add_argument('--begin_line', default=0, type=int)
    parser.add_argument('--end_line', default=None, type=int)
    parser.add_argument('--precision', default=2, type=int)
    args = parser.parse_args()

    fetch_latency_std(args.file, args.begin_line, args.end_line, precision=args.precision)


def quantize_onnx():
    import os
    from onnxruntime.quantization import quantize_dynamic, QuantType

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='float32 onnx model to quantize')
    parser.add_argument('--output_path', '--output', '-o', default=None, type=str)
    parser.add_argument('--dtype', '--data_type', choices=['uint8', 'int8'], default='uint8', type=str, help='quantization output data type')
    args = parser.parse_args()
    input_path = args.model
    output_path = args.output_path
    if output_path is None:
        name = os.path.splitext(input_path)[0]
        output_path = name + '_quant.onnx'

    dtype = QuantType.QInt8 if args.dtype == 'int8' else QuantType.QUInt8
    quantize_dynamic(input_path, output_path, activation_type=dtype)
    print(f'Successfully quantize model to {output_path}.')


def optimize_onnx_transformer():
    import os
    from onnxruntime.transformers import optimizer

    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', '-m', '-i', required=True, type=str, help='transformer onnx model to optimize')
    parser.add_argument('--output_path', '--output', '-o', default=None, type=str, help='output model path')
    parser.add_argument('--num_heads', '-a', default=12, type=int, help='number of attention heads')
    parser.add_argument('--hidden_size', default=768, type=int, help='hidden size')
    args = parser.parse_args()

    if args.output_path is None:
        args.output_path = os.path.splitext(args.model)[0] + '_opt.onnx'
    opt_model = optimizer.optimize_model(args.model, num_heads=args.num_heads, hidden_size=args.hidden_size)
    opt_model.save_model_to_file(args.output_path)
    print(f'Successfully export optimized model to {args.output_path}.')


def evaluate_onnx_cmd():
    from utils import evaluate_onnx_pipeline
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='float32 onnx model to quantize')
    parser.add_argument('--data_path', required=True, type=str, help='image net 1k dataset path')
    parser.add_argument('--threads', default=8, type=int, help='num of threads to perform inference')
    parser.add_argument('--batch_size', '-b', default=50, type=int, help='batch size')
    parser.add_argument('--num_workers', default=4, type=int, help='num of workers to load data')
    args = parser.parse_args()

    model_path = args.model 
    data_path = args.data_path
    num_threads = args.threads
    batch_size = args.batch_size
    num_workers = args.num_workers

    evaluate_onnx_pipeline(model_path, data_path, num_threads, batch_size, num_workers)


def evaluate_tflite_cmd():
    from utils import evaluate_tflite_pipeline
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='float32 onnx model to quantize')
    parser.add_argument('--data_path', required=True, type=str, help='image net 1k dataset path')
    parser.add_argument('--threads', default=8, type=int, help='num of threads to perform inference')
    parser.add_argument('--num_workers', default=4, type=int, help='num of workers to load data')
    parser.add_argument('--log', default=None, type=str, help='path to log file')
    args = parser.parse_args()

    evaluate_tflite_pipeline(args.model, args.data_path, args.threads, args.num_workers, args.log)


def evaluate_deit_cmd():
    from utils import evaluate_deit_pipeline
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--type', choices=['base', 'small', 'tiny'], help='deit model type')
    parser.add_argument('--data_path', required=True, type=str, help='image net 1k dataset path')
    parser.add_argument('--num_workers', default=4, type=int, help='num of workers to load data')
    parser.add_argument('--batch_size', default=50, type=int, help='batch size')
    parser.add_argument('--pretrained', action='store_true', dest='pretrained', help='specify to load offcial pretrained model')
    parser.add_argument('--model', default=None, type=str, help='state_dict_path')
    parser.set_defaults(pretrained=False)
    args = parser.parse_args()

    if args.pretrained is False and args.model is None:
        exit('Either load official pretrained model or specify state_dict_path')
        
    evaluate_deit_pipeline(args.type, args.model, args.data_path, 
                           pretrained=args.pretrained, 
                           batch_size=args.batch_size,
                           num_workers=args.num_workers)


def export_tf_deit():
    from modeling.models.vit import get_deit_base, get_deit_small, get_deit_tiny
    import tensorflow as tf
    def add_input(model):
        input = tf.keras.Input(shape=[3,224,224], batch_size=1)
        output = model(input)
        return tf.keras.Model(input, output)

    deit_base = add_input(get_deit_base())
    deit_small = add_input(get_deit_small())
    deit_tiny = add_input(get_deit_tiny())

    deit_base.save('models/tf_model/deit_base_patch16_224.tf')
    deit_small.save('models/tf_model/deit_small_patch16_224.tf')
    deit_tiny.save('models/tf_model/deit_tiny_patch16_224.tf')


def prune_deit_cmd():
    from utils import get_torch_deit, prune_deit_ffn_h, load_torch_deit_state_dict
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--type', choices=['base', 'small', 'tiny'], help='deit model type')
    parser.add_argument('--model', default=None, type=str, help='state_dict_path')
    parser.add_argument('--amount', required=True, help='pruning amount')
    parser.add_argument('--output', '-o', default=None, type=str, help='state_dict_path')
    parser.add_argument('--prune_type', choices=['ffn_h', 'ffn_i'], help='deit pruning type')
    args = parser.parse_args()
    
    pretrained = args.model is None
    
    model = get_torch_deit(args.type, pretrained=pretrained)
    if args.model:
        load_torch_deit_state_dict(model, args.model)

    prune_deit_ffn_h(model, args.amount)

    state_dict = dict(
        model = model.state_dict(),
        amount = args.amount,
        prune_type = args.prune_type
    )

    if args.output is None:
        args.output = f'models/torch_model/deit_{args.type}_prune_{args.prune_type}_amount{args.amount}.pth'
    torch.save(state_dict, args.output)
    print(f'Successfully save pruned deit {args.type} to {args.output}.')

def save_tfhub_vit():
    from utils import get_tfhub_vit
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--type', choices=['small', 'base'], help='vit model type')
    parser.add_argument('--output', required=True, type=str, help='saved model output path')
    args = parser.parse_args()

    model = get_tfhub_vit(args.type)
    model.save(args.output)

def eval_tf():
    from utils import evaluate_tf_pipeline
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help='tensorflow keras saved_model_path')
    parser.add_argument('--data_path', required=True, type=str, help='image net 1k dataset path')
    parser.add_argument('--threads', default=8, type=int, help='num of threads to perform inference')
    parser.add_argument('--num_workers', default=4, type=int, help='num of workers to load data')
    parser.add_argument('--channel_last', action='store_true', help='input image is channel last')
    args = parser.parse_args()

    evaluate_tf_pipeline(args.model, args.data_path, args.threads, args.num_workers, args.channel_last)

def trt_benchmark_cmd():
    import os
    from utils import trt_benchmark
    parser = argparse.ArgumentParser()
    parser.add_argument('func', help='specify the work to do.')
    parser.add_argument('--model', required=True, type=str, help="torch state_dict path")
    parser.add_argument('--input_shape', default=None, type=str, help='model input shape, currently only support one input')
    parser.add_argument('--num_runs', default=50, type=int, help='number of inference runs')
    parser.add_argument('--warmup_runs', default=20, type=int, help='number of warmup runs')
    parser.add_argument('--topk', default=None, type=int, help='take the avg of top k latency to reduce variance')
    parser.add_argument('--precision', default=2, type=int, help='the precision of latency result')
    args = parser.parse_args()

    input_shape = [int(x) for x in args.input_shape.split(',')] if args.input_shape else None
    avg_ms, std_ms = trt_benchmark(args.model, input_shape, args.num_runs, args.warmup_runs, args.topk)

    print(f'{os.path.basename(args.model)}  Avg latency: {avg_ms: .{args.precision}f} ms, Std: {std_ms: .{args.precision}f} ms.')

def main():
    func = sys.argv[1]
    if func == 'server_benchmark':
        server_benchmark()
    elif func == 'export_onnx':
        export_onnx_cmd()
    elif func == 'export_onnx_deit':
        export_onnx_deit()
    elif func == 'save_bert_encoder':
        save_bert_encoder()
    elif func == 'save_tfhub_vit':
        save_tfhub_vit()
    elif func == 'tf2tflite':
        tf2tflite_cmd()
    elif func == 'mobile_benchmark':
        mobile_benchmark()
    elif func == 'get_onnx_opset_version':
        get_onnx_opset_version_cmd()
    elif func == 'test_tf_latency':
        test_tf_latency()
    elif func == 'test_keras_latency':
        test_keras_latency()
    elif func == 'export_onnx_bert_huggingface':
        export_onnx_bert_huggingface()
    elif func == 'onnx2tflite':
        onnx2tflite_cmd()
    elif func == 'export_onnx_t2t_vit':
        export_onnx_t2t_vit()
    elif func == 'export_onnx_distilbert_huggingface':
        export_onnx_distilbert_huggingface()
    elif func == 'save_vit':
        save_vit()
    elif func == 'get_flops':
        get_flops_cmd()
    elif func == 'export_onnx_mobilenet':
        export_onnx_mobilenet()
    elif func == 'export_onnx_proxyless_mobile':
        export_onnx_proxyless_mobile()
    elif func == 'export_onnx_vit_huggingface':
        export_onnx_vit_huggingface()
    elif func == 'export_tflite_attention':
        export_tflite_attention()
    elif func == 'export_tflite_ffn':
        export_tflite_ffn()
    elif func == 'export_onnx_attention':
        export_onnx_attention()
    elif func == 'export_onnx_ffn':
        export_onnx_ffn()
    elif func == 'fetch_latency_std':
        fetch_latency_std_cmd()
    elif func == 'export_pb_ffn':
        export_pb_ffn()
    elif func == 'export_onnx_dense':
        export_onnx_dense()
    elif func == 'quantize_onnx':
        quantize_onnx()
    elif func == 'eval_onnx':
        evaluate_onnx_cmd()
    elif func in ['opt_onnx_transformer', 'opt_onnx', 'optimize_onnx']:
        optimize_onnx_transformer()
    elif func == 'export_tf_deit':
        export_tf_deit()
    elif func == 'eval_tflite':
        evaluate_tflite_cmd()
    elif func == 'eval_deit':
        evaluate_deit_cmd()
    elif func == 'eval_tf':
        eval_tf()
    elif func == 'prune_deit':
        prune_deit_cmd()
    elif func == 'export_onnx_swin':
        export_onnx_swin()
    elif func == 'tf2tflite_dir':
        tf2tflite_dir_cmd()
    elif func == 'trt_benchmark':
        trt_benchmark_cmd()


if __name__ == '__main__':
    main()