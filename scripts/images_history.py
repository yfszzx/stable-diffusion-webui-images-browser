import os
import re
import shutil
import time
import stat
import json
import random
import gradio as gr
import modules.extras
import modules.ui
from modules.shared import opts, cmd_opts
from modules.ui_components import ToolButton
from modules import shared, scripts
from modules import script_callbacks
from PIL import Image
from pathlib import Path
from send2trash import send2trash
from typing import List, Tuple

favorite_tab_name = "Favorites"
tabs_list = ["txt2img", "img2img", "txt2img-grids", "img2img-grids", "Extras", favorite_tab_name, "Others"] #txt2img-grids and img2img-grids added by HaylockGrant
num_of_imgs_per_page = 0
loads_files_num = 0
path_recorder_filename = os.path.join(scripts.basedir(), "path_recorder.txt")
path_recorder_filename_tmp = f"{path_recorder_filename}.tmp"
image_ext_list = [".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"]
none_select = "Nothing selected"
refresh_symbol = '\U0001f504'  # 🔄
up_symbol = '\U000025b2'  # ▲
down_symbol = '\U000025bc'  # ▼
current_depth = 0

def delete_recycle(filename):
    if opts.images_delete_recycle:
        send2trash(filename)
    else:
        os.remove(filename)
    return

def img_path_subdirs_get(img_path):
    subdirs = []
    subdirs.append(none_select)
    for item in os.listdir(img_path):
        item_path = os.path.join(img_path, item)
        if os.path.isdir(item_path):
            subdirs.append(item_path)
    return gr.update(choices=subdirs)

def img_path_add_remove(img_dir, path_recorder, add_remove, img_path_depth):
    if add_remove == "add" or (add_remove == "remove" and img_dir in path_recorder):
        if os.path.exists(path_recorder_filename_tmp):
            os.remove(path_recorder_filename_tmp)
        if add_remove == "add":
            path_recorder[img_dir] = {
                "depth": int(img_path_depth),
                "path_display": f"{img_dir} [{int(img_path_depth)}]"
            }
        else:
            del path_recorder[img_dir]
        path_recorder = {key: value for key, value in sorted(path_recorder.items(), key=lambda x: x[0].lower())}
        with open(path_recorder_filename_tmp, "w") as f:
            json.dump(path_recorder, f, indent=4)
        os.replace(path_recorder_filename_tmp, path_recorder_filename)
        path_recorder_formatted = [value.get("path_display") for key, value in path_recorder.items()]
    return path_recorder, gr.update(choices=path_recorder_formatted, value=path_recorder[img_dir]["path_display"])

def sort_order_flip(turn_page_switch, sort_order):
    if sort_order == up_symbol:
        sort_order = down_symbol
    else:
        sort_order = up_symbol
    return 1, -turn_page_switch, sort_order

def read_path_recorder(path_recorder, path_recorder_formatted):
    if os.path.exists(path_recorder_filename):
        try:
            with open(path_recorder_filename) as f:
                path_recorder = json.load(f)
        except json.JSONDecodeError:
            with open(path_recorder_filename) as f:
                path = f.readline().rstrip("\n")
                while len(path) > 0:
                    path_recorder[path] = {
                        "depth": 0,
                        "path_display": f"{path} [0]"
                    }
                    path = f.readline().rstrip("\n")
    path_recorder_formatted = [value.get("path_display") for key, value in path_recorder.items()]
    path_recorder_formatted = sorted(path_recorder_formatted, key=lambda x: x.lower())
    return path_recorder, path_recorder_formatted

def pure_path(path):
    match = re.search(r" \[(\d+)\]$", path)
    if match:
        path = path[:match.start()]
        depth = int(match.group(1))
    else:
        depth = 0
    return path, depth

def history2path(img_path_history):
    img_path, _ = pure_path(img_path_history)
    return img_path

def reduplicative_file_move(src, dst):
    def same_name_file(basename, path):
        name, ext = os.path.splitext(basename)
        f_list = os.listdir(path)
        max_num = 0
        for f in f_list:
            if len(f) <= len(basename):
                continue
            f_ext = f[-len(ext):] if len(ext) > 0 else ""
            if f[:len(name)] == name and f_ext == ext:                
                if f[len(name)] == "(" and f[-len(ext)-1] == ")":
                    number = f[len(name)+1:-len(ext)-1]
                    if number.isdigit():
                        if int(number) > max_num:
                            max_num = int(number)
        return f"{name}({max_num + 1}){ext}"
    name = os.path.basename(src)
    save_name = os.path.join(dst, name)
    if not os.path.exists(save_name):
        if opts.images_copy_image:
            shutil.copy2(src, dst)
        else:
            shutil.move(src, dst)
    else:
        name = same_name_file(name, dst)
        if opts.images_copy_image:
            shutil.copy2(src, os.path.join(dst, name))
        else:
            shutil.move(src, os.path.join(dst, name))

def save_image(file_name):
    if file_name is not None and os.path.exists(file_name):
        reduplicative_file_move(file_name, opts.outdir_save)
        return "<div style='color:#999'>Moved to favorites</div>"
    else:
        return "<div style='color:#999'>Image not found (may have been already moved)</div>"

def delete_image(delete_num, name, filenames, image_index, visible_num):
    if name == "":
        return filenames, delete_num
    else:
        delete_num = int(delete_num)
        visible_num = int(visible_num)
        image_index = int(image_index)
        index = list(filenames).index(name)
        i = 0
        new_file_list = []
        for name in filenames:
            if i >= index and i < index + delete_num:
                if os.path.exists(name):
                    if visible_num == image_index:
                        new_file_list.append(name)
                        i += 1
                        continue
                    if opts.images_delete_message:
                        print(f"Deleting file {name}")
                    delete_recycle(name)
                    visible_num -= 1
                    txt_file = os.path.splitext(name)[0] + ".txt"
                    if os.path.exists(txt_file):
                        delete_recycle(txt_file)
                else:
                    print(f"File does not exist {name}")
            else:
                new_file_list.append(name)
            i += 1
    return new_file_list, 1, visible_num

def traverse_all_files(curr_path, image_list, tabname_box, img_path_depth) -> List[Tuple[str, os.stat_result, str, int]]:
    global current_depth
    if curr_path == "":
        return image_list
    f_list = [(os.path.join(curr_path, entry.name), entry.stat()) for entry in os.scandir(curr_path)]
    for f_info in f_list:
        fname, fstat = f_info
        if os.path.splitext(fname)[1] in image_ext_list:
            image_list.append(f_info)
        elif stat.S_ISDIR(fstat.st_mode):
            if opts.images_history_with_subdirs or (tabname_box == "Others" and img_path_depth != 0 and (current_depth < img_path_depth or img_path_depth < 0)):
                current_depth = current_depth + 1
                image_list = traverse_all_files(fname, image_list, tabname_box, img_path_depth)
                current_depth = current_depth - 1
    return image_list

def get_all_images(dir_name, sort_by, sort_order, keyword, tabname_box, img_path_depth):
    global current_depth
    current_depth = 0
    fileinfos = traverse_all_files(dir_name, [], tabname_box, img_path_depth)
    keyword = keyword.strip(" ")
    if len(keyword) != 0:
        fileinfos = [x for x in fileinfos if keyword.lower() in x[0].lower()]
    if sort_by == "date":
        if sort_order == up_symbol:
            fileinfos = sorted(fileinfos, key=lambda x: x[1].st_mtime)
        else:
            fileinfos = sorted(fileinfos, key=lambda x: -x[1].st_mtime)
    elif sort_by == "path name":
        if sort_order == up_symbol:
            fileinfos = sorted(fileinfos)
        else:
            fileinfos = sorted(fileinfos, reverse=True)
    elif sort_by == "aesthetic_score":
        if sort_order == up_symbol:
            fileinfos = sorted(fileinfos, key=lambda x: get_image_aesthetic_score(x[0]))
        else:
            fileinfos = sorted(fileinfos, key=lambda x: -get_image_aesthetic_score(x[0]))
    elif sort_by == "random":
        random.shuffle(fileinfos)

    filenames = [finfo[0] for finfo in fileinfos]
    return filenames

def get_image_aesthetic_score(img_path):
    if not os.path.exists(img_path):
        return 0
    img = Image.open(img_path)

    try:
        return float(img.info['aesthetic_score'])
    except KeyError:
        return 0

def get_image_page(img_path, page_index, filenames, keyword, sort_by, sort_order, tabname_box, img_path_depth):
    img_path, _ = pure_path(img_path)
    if not cmd_opts.administrator:
        head = os.path.abspath(".")
        abs_path = os.path.abspath(img_path)
        if len(abs_path) < len(head) or abs_path[:len(head)] != head:
            warning = f"You have not permission to visit {img_path}. If you want visit all directories, add command line argument option '--administrator', <a style='color:#990' href='https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Command-Line-Arguments-and-Settings'>More detail here</a>"
            return None, 0, None, "", "", "", None, None, warning
    if page_index == 1 or page_index == 0 or len(filenames) == 0:
        filenames = get_all_images(img_path, sort_by, sort_order, keyword, tabname_box, img_path_depth)
    page_index = int(page_index)
    length = len(filenames)
    max_page_index = length // num_of_imgs_per_page + 1
    page_index = max_page_index if page_index == -1 else page_index
    page_index = 1 if page_index < 1 else page_index
    page_index = max_page_index if page_index > max_page_index else page_index
    idx_frm = (page_index - 1) * num_of_imgs_per_page
    image_list = filenames[idx_frm:idx_frm + num_of_imgs_per_page]
    
    visible_num = num_of_imgs_per_page if  idx_frm + num_of_imgs_per_page < length else length % num_of_imgs_per_page 
    visible_num = num_of_imgs_per_page if visible_num == 0 else visible_num

    load_info = "<div style='color:#999' align='center'>"
    load_info += f"{length} images in this directory, divided into {int((length + 1) // num_of_imgs_per_page  + 1)} pages"
    load_info += "</div>"
    return filenames, gr.update(value=page_index, label=f"Page Index (of {max_page_index} pages)"), image_list,  "", "",  "", visible_num, load_info

def show_image_info(tabname_box, num, page_index, filenames):
    file = filenames[int(num) + int((page_index - 1) * num_of_imgs_per_page)]   
    tm =   "<div style='color:#999' align='right'>" + time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(os.path.getmtime(file))) + "</div>"
    return file, tm, num, file, ""

def change_dir(img_dir, path_recorder, load_switch, img_path_history, img_path_depth, img_path):
    warning = None
    img_path, _ = pure_path(img_path)
    img_path_depth_org = img_path_depth
    if img_dir == none_select:
        return warning, gr.update(visible=False), img_path_history, path_recorder, load_switch, img_path, img_path_depth
    else:
        img_dir, img_path_depth = pure_path(img_dir)
        try:
            if not cmd_opts.administrator:        
                head = os.path.abspath(".")
                abs_path = os.path.abspath(img_dir)
                if len(abs_path) < len(head) or abs_path[:len(head)] != head:
                    warning = f"You have no permission to visit {img_dir}. If you want to visit all directories, add command line argument option '--administrator', <a style='color:#990' href='https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Command-Line-Arguments-and-Settings'>More details here</a>"
        except:
            pass  
        if warning is None:
            try:
                if os.path.exists(img_dir):
                    try:
                        f = os.listdir(img_dir)                
                    except:
                        warning = f"'{img_dir} is not a directory"
                else:
                    warning = "The directory does not exist"
            except:
                warning = "The format of the directory is incorrect"   
        if warning is None: 
            return "", gr.update(visible=True), img_path_history, path_recorder, img_dir, img_dir, img_path_depth
        else:
            return warning, gr.update(visible=False), img_path_history, path_recorder, load_switch, img_path, img_path_depth_org

def update_move_text(unused):
    return f'{"Move" if not opts.images_copy_image else "Copy"} to favorites'

def create_tab(tabname):
    custom_dir = False
    path_recorder = {}
    path_recorder_formatted = []
    if tabname == "txt2img":
        dir_name = opts.outdir_txt2img_samples
    elif tabname == "img2img":
        dir_name = opts.outdir_img2img_samples
    elif tabname == "txt2img-grids":    #added by HaylockGrant to add a new tab for grid images
        dir_name = opts.outdir_txt2img_grids
    elif tabname == "img2img-grids":    #added by HaylockGrant to add a new tab for grid images
        dir_name = opts.outdir_img2img_grids
    elif tabname == "Extras":
        dir_name = opts.outdir_extras_samples
    elif tabname == favorite_tab_name:
        dir_name = opts.outdir_save
    else:
        custom_dir = True
        dir_name = None        
        path_recorder, path_recorder_formatted = read_path_recorder(path_recorder, path_recorder_formatted)

    if not custom_dir:
        dir_name = str(Path(dir_name))
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)

    with gr.Row(visible= custom_dir): 
        with gr.Column(scale=10):
            img_path = gr.Textbox(dir_name, label="Images directory", placeholder="Input images directory", interactive=custom_dir)  
        with gr.Column(scale=1):
            img_path_depth = gr.Number(value="0", label="Sub directory depth")
        with gr.Column(scale=1):
            img_path_save_button = gr.Button(value="Add to / replace in saved directories")

    with gr.Row(visible= custom_dir): 
        with gr.Column(scale=10):
            img_path_history = gr.Dropdown(choices=path_recorder_formatted, label="Saved directories")
        with gr.Column(scale=1):
            img_path_remove_button = gr.Button(value="Remove from saved directories")
            path_recorder = gr.State(path_recorder)

    with gr.Row(visible= custom_dir): 
        with gr.Column(scale=10):
            img_path_subdirs = gr.Dropdown(choices=[none_select], value=none_select, label="Sub directories", interactive=True, elem_id="img_path_subdirs")
        with gr.Column(scale=1):
            img_path_subdirs_button = gr.Button(value="Get sub directories")
        
    with gr.Row(visible= not custom_dir, elem_id=tabname + "_images_history") as main_panel:
        with gr.Column():  
            with gr.Row():    
                with gr.Column(scale=2):     
                    with gr.Row():       
                        first_page = gr.Button('First Page')
                        prev_page = gr.Button('Prev Page')
                        page_index = gr.Number(value=1, label="Page Index")
                        refresh_index_button = ToolButton(value=refresh_symbol)
                        next_page = gr.Button('Next Page')
                        end_page = gr.Button('End Page') 
                    history_gallery = gr.Gallery(show_label=False, elem_id=tabname + "_images_history_gallery").style(grid=opts.images_history_page_columns)
                    with gr.Row() as delete_panel:
                        with gr.Column(scale=1):
                            delete_num = gr.Number(value=1, interactive=True, label="delete next")
                        with gr.Column(scale=3):
                            delete = gr.Button('Delete', elem_id=tabname + "_images_history_del_button")
                        
                with gr.Column(): 
                    with gr.Row():  
                        sort_by = gr.Radio(value="date", choices=["path name", "date", "aesthetic_score", "random"], label="sort by")
                        sort_order = ToolButton(value=down_symbol)
                    with gr.Row():  
                        keyword = gr.Textbox(value="", label="keyword")                 
                    with gr.Row():
                        with gr.Column():
                            img_file_info = gr.Textbox(label="Generate Info", interactive=False, lines=6)
                            img_file_name = gr.Textbox(value="", label="File Name", interactive=False)
                            img_file_time= gr.HTML()
                    with gr.Row(elem_id=tabname + "_images_history_button_panel") as button_panel:
                        if tabname != favorite_tab_name:
                            save_btn = gr.Button(f'{"Move" if not opts.images_copy_image else "Copy"} to favorites')
                        try:
                            send_to_buttons = modules.generation_parameters_copypaste.create_buttons(["txt2img", "img2img", "inpaint", "extras"])
                        except:
                            pass
                    with gr.Row():
                        collected_warning = gr.HTML()                       
                            
                    # hidden items
                    with gr.Row(visible=False): 
                        renew_page = gr.Button("Renew Page", elem_id=tabname + "_images_history_renew_page")
                        visible_img_num = gr.Number()                     
                        tabname_box = gr.Textbox(tabname)
                        image_index = gr.Textbox(value=-1)
                        set_index = gr.Button('set_index', elem_id=tabname + "_images_history_set_index")
                        filenames = gr.State([])
                        all_images_list = gr.State()
                        hidden = gr.Image(type="pil")
                        info1 = gr.Textbox()
                        info2 = gr.Textbox()
                        load_switch = gr.Textbox(value="load_switch", label="load_switch")
                        turn_page_switch = gr.Number(value=1, label="turn_page_switch")
                        img_path_add = gr.Textbox(value="add")
                        img_path_remove = gr.Textbox(value="remove")
    with gr.Row():                 
        warning_box = gr.HTML() 

    change_dir_outputs = [warning_box, main_panel, img_path_history, path_recorder, load_switch, img_path, img_path_depth]
    img_path.submit(change_dir, inputs=[img_path, path_recorder, load_switch, img_path_history, img_path_depth, img_path], outputs=change_dir_outputs)
    img_path_history.change(change_dir, inputs=[img_path_history, path_recorder, load_switch, img_path_history, img_path_depth, img_path], outputs=change_dir_outputs)
    img_path_history.change(history2path, inputs=[img_path_history], outputs=[img_path])

    #delete
    delete.click(delete_image, inputs=[delete_num, img_file_name, filenames, image_index, visible_img_num], outputs=[filenames, delete_num, visible_img_num])
    delete.click(fn=None, _js="images_history_delete", inputs=[delete_num, tabname_box, image_index], outputs=None) 
    if tabname != favorite_tab_name: 
        save_btn.click(save_image, inputs=[img_file_name], outputs=[collected_warning])
        img_file_name.change(fn=update_move_text, inputs=[img_file_name], outputs=[save_btn])

    #turn page
    first_page.click(lambda s:(1, -s) , inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    next_page.click(lambda p, s: (p + 1, -s), inputs=[page_index, turn_page_switch], outputs=[page_index, turn_page_switch])
    prev_page.click(lambda p, s: (p - 1, -s), inputs=[page_index, turn_page_switch], outputs=[page_index, turn_page_switch])
    end_page.click(lambda s: (-1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])    
    load_switch.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    keyword.submit(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    sort_by.change(lambda s:(1, -s), inputs=[turn_page_switch], outputs=[page_index, turn_page_switch])
    page_index.submit(lambda s: -s, inputs=[turn_page_switch], outputs=[turn_page_switch])
    renew_page.click(lambda s: -s, inputs=[turn_page_switch], outputs=[turn_page_switch])
    refresh_index_button.click(lambda p, s:(p, -s), inputs=[page_index, turn_page_switch], outputs=[page_index, turn_page_switch])
    img_path_depth.change(lambda s: -s, inputs=[turn_page_switch], outputs=[turn_page_switch])

    turn_page_switch.change(
        fn=get_image_page, 
        inputs=[img_path, page_index, filenames, keyword, sort_by, sort_order, tabname_box, img_path_depth], 
        outputs=[filenames, page_index, history_gallery, img_file_name, img_file_time, img_file_info, visible_img_num, warning_box]
    )
    turn_page_switch.change(fn=None, inputs=[tabname_box], outputs=None, _js="images_history_turnpage")
    turn_page_switch.change(fn=lambda:(gr.update(visible=False), gr.update(visible=False)), inputs=None, outputs=[delete_panel, button_panel])

    sort_order.click(
        fn=sort_order_flip,
        inputs=[turn_page_switch, sort_order],
        outputs=[page_index, turn_page_switch, sort_order]
    )

    # Others
    img_path_subdirs_button.click(
        fn=img_path_subdirs_get, 
        inputs=[img_path], 
        outputs=[img_path_subdirs]
    )
    img_path_subdirs.change(
        fn=change_dir, 
        inputs=[img_path_subdirs, path_recorder, load_switch, img_path_history, img_path_depth, img_path], 
        outputs=change_dir_outputs
    )
    img_path_save_button.click(
        fn=img_path_add_remove, 
        inputs=[img_path, path_recorder, img_path_add, img_path_depth], 
        outputs=[path_recorder, img_path_history]
    )
    img_path_remove_button.click(
        fn=img_path_add_remove, 
        inputs=[img_path, path_recorder, img_path_remove], 
        outputs=[path_recorder, img_path_history]
    )

    # other functions
    set_index.click(show_image_info, _js="images_history_get_current_img", inputs=[tabname_box, image_index, page_index, filenames], outputs=[img_file_name, img_file_time, image_index, hidden])
    set_index.click(fn=lambda:(gr.update(visible=True), gr.update(visible=True)), inputs=None, outputs=[delete_panel, button_panel]) 
    img_file_name.change(fn=lambda : "", inputs=None, outputs=[collected_warning])  
   
    hidden.change(fn=modules.extras.run_pnginfo, inputs=[hidden], outputs=[info1, img_file_info, info2])

    try:
        modules.generation_parameters_copypaste.bind_buttons(send_to_buttons, hidden, img_file_info)
    except:
        pass

def on_ui_tabs():
    global num_of_imgs_per_page
    global loads_files_num
    num_of_imgs_per_page = int(opts.images_history_page_columns * opts.images_history_page_rows)
    loads_files_num = int(opts.images_history_pages_perload * num_of_imgs_per_page)
    with gr.Blocks(analytics_enabled=False) as images_history:
        with gr.Tabs(elem_id="images_history_tab") as tabs:
            for tab in tabs_list:
                with gr.Tab(tab):
                    with gr.Blocks(analytics_enabled=False) :
                        create_tab(tab)
        gr.Checkbox(opts.images_history_preload, elem_id="images_history_preload", visible=False)         
        gr.Textbox(",".join(tabs_list), elem_id="images_history_tabnames_list", visible=False) 
    return (images_history , "Image Browser", "images_history"),

def on_ui_settings():
    section = ('images-history', "Images Browser")
    shared.opts.add_option("images_history_with_subdirs", shared.OptionInfo(False, "Include images in sub directories", section=section))
    shared.opts.add_option("images_history_preload", shared.OptionInfo(False, "Preload images at startup", section=section))
    shared.opts.add_option("images_copy_image", shared.OptionInfo(False, "Move to favorites button copies instead of moving", section=section))
    shared.opts.add_option("images_delete_message", shared.OptionInfo(True, "Print image deletion messages to the console", section=section))
    shared.opts.add_option("images_delete_recycle", shared.OptionInfo(False, "Use recycle bin when deleting images", section=section))
    shared.opts.add_option("images_history_page_columns", shared.OptionInfo(6, "Number of columns on the page", section=section))
    shared.opts.add_option("images_history_page_rows", shared.OptionInfo(6, "Number of rows on the page", section=section))
    shared.opts.add_option("images_history_pages_perload", shared.OptionInfo(20, "Minimum number of pages per load", section=section))


script_callbacks.on_ui_settings(on_ui_settings)
script_callbacks.on_ui_tabs(on_ui_tabs)

#TODO:
#move by arrow key
#generate info in txt
