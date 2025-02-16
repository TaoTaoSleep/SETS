import os
import re
import time
import requests
import json
import chardet
import traceback
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import sys

if getattr(sys, 'frozen', False):
    # 打包后使用exe所在目录
    script_dir = os.path.dirname(sys.executable)
else:
    # 开发时使用脚本所在目录
    script_dir = os.path.dirname(__file__)

output_folder = os.path.join(script_dir, "翻译")
BACKUP_FOLDER = os.path.join(script_dir, "翻译备份")
CONFIG_FILE = os.path.join(script_dir, "api_config.json")
MAX_RETRIES = 3
REQUEST_DELAY = 4.0
TIMEOUT_SECONDS = 55
TRANSLATABLE_TAGS = ["DisplayName", "Description", "Tooltip", "value"]
SKIP_KEYWORDS = ["DisplayName", "Item", "Description", "Group"]
CACHE_FILE = os.path.join(script_dir, "translation_cache.json")
ADD_LANGUAGE_TAG = False

# 从modid.txt文件的第一行读取MOD_FOLDER的路径
MOD_ID_LIST_FILE = os.path.join(script_dir, "modid.txt")

# 检查 modid.txt 文件是否存在，如果不存在则生成一个默认的文件
if not os.path.exists(MOD_ID_LIST_FILE):
    default_modid_content = """# 请将第一行设置为 MOD 文件夹的路径
# 例如: C:\\path\\to\\mods
# 请将第二行以下设置为需要翻译的 mod ID
# 例如: mod_id_1
# mod_id_2
填写前删除所有内容
"""
    with open(MOD_ID_LIST_FILE, "w", encoding="utf-8") as f:
        f.write(default_modid_content)
    print(f"已生成 modid.txt 文件: {MOD_ID_LIST_FILE}")
    print("请根据需要修改配置文件中的 MOD 文件夹路径和 mod ID。")
    input("按 Enter 键继续运行脚本...")

with open(MOD_ID_LIST_FILE, "r", encoding="utf-8") as f:
    MOD_FOLDER = f.readline().strip()

api_call_counter = 0
translation_stats = {"total": 0, "success": 0, "failed": 0}

class AlibabaBatchTranslator:
    def __init__(self, api_key, api_url):
        self.api_key = api_key
        self.url = api_url
        self.cache = self.load_cache()

    def load_cache(self):
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_cache(self):
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"缓存保存失败: {e}")

    def translate_text(self, tag, text):
        global api_call_counter
        api_call_counter += 1
        cache_key = f"{tag}:{text}"

        if cache_key in self.cache:
            return self.cache[cache_key]

        payload = {
            "model": "qwen-max-latest",
            "input": {
                "prompt": (
                    "你是一个专业的技术文档翻译引擎，请严格遵循以下规则：\n"
                    "将所有语言都汉化为中文\n"
                    "遇到类似层级结构名称（如'项目-资源-石头'），乱码，符号时不要进行翻译，原文输出\n"
                    "不要添加任何说明符号\n"
                    "待翻译内容：\n" + text
                )
            }
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(self.url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS)
                response.raise_for_status()
                result = response.json()

                if "output" in result and "text" in result["output"]:
                    translated = self.clean_translation(result["output"]["text"].strip())
                    # 检查翻译结果是否为空
                    if not translated:
                        print(f"警告: 翻译结果为空 (Tag: {tag}, Original: {text})")
                        raise Exception("API 返回的翻译结果为空")
                    
                    self.cache[cache_key] = translated
                    self.save_cache()
                    return translated
                raise Exception(f"API返回异常: {result}")
            except Exception as e:
                print(f"翻译尝试 {attempt + 1} 失败: {e}")
                time.sleep(REQUEST_DELAY)
        raise Exception("达到最大重试次数")

    def clean_translation(self, text):
        patterns = [
            r"^(显示名称|描述|工具提示)[：:]?\s*",
            r"</?\s*(显示名称|描述|工具提示|值|数据)\s*>",
            r"<(\w+)\s*名称=",
            r"<\/값>",
            r"<\/数据>"
        ]
        for pattern in patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        return text.strip()

def detect_encoding(file_path):
    try:
        with open(file_path, 'rb') as f:
            raw = f.read(4096)
        detected = chardet.detect(raw)
        encoding = detected['encoding'] or 'utf-8'
        confidence = detected.get('confidence', 0)
        
        if confidence < 0.7:
            encoding = 'utf-8'
        
        return encoding
    except Exception as e:
        print(f"检测编码失败: {e}, 默认使用 utf-8")
        return 'utf-8'

def load_mod_ids(mod_id_list_file):
    with open(mod_id_list_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        MOD_FOLDER = lines[0].strip()  # 第一行是MOD_FOLDER路径
        return [line.strip() for line in lines[1:] if line.strip()]  # 从第二行开始读取mod_id

def parse_translatable_content(file_path):
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors='replace') as f:
        content = f.read()

    pattern = re.compile(r'''
        (?P<full_tag>
            <(?P<tag>data)\s+name="[^"]+"[^>]*>     # data标签开始
            \s*<value>\s*(?P<value>.*?)\s*</value>  # value内容
            \s*</data>                              # data标签结束
        )
        |
        <(?P<simple_tag>DisplayName|Description|Tooltip)>
            \s*(?P<simple_text>.*?)\s*
        </(?P=simple_tag)>
    ''', re.DOTALL | re.IGNORECASE | re.VERBOSE)

    matches = []
    for match in pattern.finditer(content):
        if match.group('full_tag'):
            matches.append(('value', match.group('value').strip(), match.group('full_tag')))
        elif match.group('simple_tag'):
            matches.append((match.group('simple_tag'), match.group('simple_text').strip(), None))

    filtered = []
    for tag, text, context in matches:
        if any(kw in text for kw in SKIP_KEYWORDS) or is_chinese(text) or len(text) < 2:
            continue
        filtered.append((tag, text, context))
    return filtered

def replace_translated_content(file_path, translations, add_language_tag):
    encoding = detect_encoding(file_path)
    with open(file_path, "r", encoding=encoding, errors='replace') as f:
        content = f.read()

    replacements = []
    for tag, original, translated, context in translations:
        try:
            if tag == 'value' and context:
                original_text = re.escape(original)
                new_content = re.sub(r'<value>\s*%s\s*</value>' % original_text, 
                                   f'<value>{translated}</value>', 
                                   context, 
                                   flags=re.DOTALL)
                replacements.append((re.escape(context), new_content))
            else:
                pattern = fr'<{tag}>\s*{re.escape(original)}\s*</{tag}>'
                replacements.append((pattern, f'<{tag}>{translated}</{tag}>'))
        except Exception as e:
            traceback.print_exc()

    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)

    rel_path = os.path.relpath(file_path, MOD_FOLDER)
    if add_language_tag and file_path.endswith('.resx'):
        base, ext = os.path.splitext(rel_path)
        rel_path = f"{base}.zh-CN{ext}"
    
    output_path = os.path.join(output_folder, rel_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8", newline='\n') as f:
        f.write(content)

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def batch_translate(translator, batch):
    try:
        results = []
        for tag, text, context in batch:
            translated = translator.translate_text(tag, text)
            results.append((tag, text, translated, context))
        return results
    except Exception as e:
        print(f"批量翻译失败: {str(e)}")
        return [(item[0], item[1], item[1], item[2]) for item in batch]

def log_translation(mod_id, original, translated, file_path, status="Success", error=None):
    global translation_stats
    if is_chinese(original): 
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] ModID:{mod_id} | Status:{status}\n"
    log_entry += f"Original: {original or 'N/A'}\nTranslated: {translated or 'N/A'}\n"
    if error:
        log_entry += f"Error: {error}\n"
    log_entry += f"File: {file_path}\n{'='*50}\n"
    
    # 更新统计信息
    translation_stats["total"] += 1
    if status == "Success":
        translation_stats["success"] += 1
    else:
        translation_stats["failed"] += 1

    # 确保日志写入成功
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"日志写入失败: {e}")

def process_file(mod_id, file_path, translators, add_language_tag):
    try:
        backup_path = os.path.join(BACKUP_FOLDER, os.path.relpath(file_path, MOD_FOLDER))
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(file_path, "rb") as src, open(backup_path, "wb") as dst:
            dst.write(src.read())

        matches = parse_translatable_content(file_path)
        if not matches:
            print(f"警告: 文件 {file_path} 没有可翻译内容。")
            return

        batches = [matches[i::len(translators)] for i in range(len(translators))]
        all_translations = []

        with ThreadPoolExecutor(max_workers=len(translators)) as executor:
            futures = []
            for i, batch in enumerate(batches):
                if batch:
                    futures.append(executor.submit(
                        process_batch, 
                        translators[i], 
                        batch, 
                        mod_id, 
                        file_path
                    ))

            for future in as_completed(futures):
                batch_result = future.result()
                all_translations.extend(batch_result)
                for item in batch_result:
                    log_translation(mod_id, item[1], item[2], file_path)

        replace_translated_content(file_path, all_translations, add_language_tag)

    except Exception as e:
        log_translation(mod_id, "", "", file_path, "Failed", str(e))
        traceback.print_exc()

def process_batch(translator, batch, mod_id, file_path):
    try:
        return batch_translate(translator, batch)
    except Exception as e:
        print(f"批处理失败: {str(e)}")
        return [(item[0], item[1], item[1], item[2]) for item in batch]

def generate_api_config():
    default_config = {
        "api_keys": [
            "在此处填入秘钥",
            " ", 
            " "
        ],
        "api_url": "在此处填入URL",
        "__usage_instructions__": "一行一个，可多行，推荐阿里云百炼 API。API 数量越多，翻译速度越快。请将实际的 API 密钥和 URL 填写到对应位置。"
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(default_config, f, ensure_ascii=False, indent=2)
    print(f"已生成 API 配置文件: {CONFIG_FILE}")
    print("请根据需要修改配置文件中的 API 密钥和 URL。")

def load_api_config():
    if not os.path.exists(CONFIG_FILE):
        generate_api_config()
        input("按 Enter 键继续运行脚本...")
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    api_keys = config.get("api_keys", [])
    api_url = config.get("api_url", "").strip()

    # 检查API密钥和URL是否为空或无效
    if not api_keys or all(not k.strip() for k in api_keys):
        print("错误: API密钥列表为空或无效。请在配置文件中提供有效的API密钥。")
        print("请修改 api_config.json 文件中的 API 密钥后重试。")
        exit(1)
    
    if not api_url:
        print("错误: API URL为空或无效。请在配置文件中提供有效的API URL。")
        print("请修改 api_config.json 文件中的 API URL 后重试。")
        exit(1)
    
    # 检查API密钥和URL是否为默认值
    if api_keys == ["在此处填入秘钥", " ", " "] or api_url == "在此处填入URL":
        print("警告: API密钥和URL尚未修改。请修改 api_config.json 文件中的 API 密钥和 URL 后重试。")
        exit(1)
    
    return api_keys, api_url

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--add-language-tag', action='store_true')
    args = parser.parse_args()
    
    os.makedirs(output_folder, exist_ok=True)
    os.makedirs(BACKUP_FOLDER, exist_ok=True)

    # 检查并创建 OUTPUT_FOLDER 文件夹
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"已创建翻译文件夹: {output_folder}")

    api_keys, api_url = load_api_config()
    translators = [AlibabaBatchTranslator(k, api_url) for k in api_keys]
    mod_ids = load_mod_ids(MOD_ID_LIST_FILE)

    # 检查modid.txt文件内容是否被修改
    with open(MOD_ID_LIST_FILE, "r", encoding="utf-8") as f:
        mod_id_content = f.read().strip()
    if mod_id_content == """# 请将第一行设置为 MOD 文件夹的路径
# 例如: C:\\path\\to\\mods
# 请将第二行以下设置为需要翻译的 mod ID
# 例如: mod_id_1
# mod_id_2
填写前删除所有内容""":
        print("警告: modid.txt 文件尚未修改。请根据需要修改配置文件中的 MOD 文件夹路径和 mod ID。")
        exit(1)

    if not mod_ids:
        print("错误: modid.txt 文件中没有有效的 mod ID。")
        print("请在 modid.txt 文件中添加有效的 mod ID 后重试。")
        exit(1)

    # 动态生成日志文件名
    global LOG_FILE
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    LOG_FILE = os.path.join(output_folder, f"translation_log_{timestamp}.txt")

    start_time = time.time()

    for mod_id in tqdm(mod_ids, desc="处理Mod"):
        mod_path = os.path.join(MOD_FOLDER, mod_id)
        if not os.path.exists(mod_path):
            continue

        files = []
        for root, _, filenames in os.walk(mod_path):
            files.extend(os.path.join(root, f) 
                for f in filenames if f.endswith(('.sbc', '.resx')))

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_file, mod_id, f, translators, args.add_language_tag) 
                      for f in files]
            for future in tqdm(as_completed(futures), total=len(files), desc=mod_id):
                future.result()

    end_time = time.time()
    elapsed_time = end_time - start_time

    # 记录翻译统计信息
    summary = (
        f"\n{'='*50}\n"
        f"翻译完成！\n"
        f"总翻译条目数: {translation_stats['total']}\n"
        f"成功翻译条目数: {translation_stats['success']}\n"
        f"失败翻译条目数: {translation_stats['failed']}\n"
        f"运行时间: {elapsed_time:.2f} 秒\n"
        f"{'='*50}\n"
    )
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(summary)
    except Exception as e:
        print(f"统计信息写入失败: {e}")

if __name__ == "__main__":
    main()