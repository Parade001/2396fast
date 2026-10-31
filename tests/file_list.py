import os
import shutil
cwd =  os.getcwd()

FILES =  [ os.path.join(cwd, f) for f in os.listdir('.') if f.endswith('.xlsx')]

def move_files(file_name: str):
    # 移动文件到指定目录
    try:
        
        shutil.move(os.path.join(cwd, file_name), os.path.join(cwd, 'output'))
    except Exception as e:
        print(f"移动文件失败: {str(e)}")
        raise
    



