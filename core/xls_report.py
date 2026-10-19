"""Excel 97–2003 BIFF report matching the supplied six-column reference."""
import json
import copy
import re
import sys
from pathlib import Path
import xlwt

_CONTROL=re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

def text(value):
    if value is None:return ''
    return _CONTROL.sub(lambda m:f'\\x{ord(m[0]):02x}',str(value))

def _styles(layout):
    fonts=[]
    for source in layout['fonts']:
        font=xlwt.Font()
        for key in ('name','height','colour_index','bold','italic','struck_out','outline','shadow','family','escapement'):
            if key in source:setattr(font,key,source[key])
        font.charset=source.get('character_set',0);font.underline=source.get('underline_type',0);fonts.append(font)
    result=[]
    for source in layout['styles']:
        style=xlwt.XFStyle();style.font=fonts[source['font_index']];style.num_format_str=source['format']
        a=source['alignment'];alignment=style.alignment
        for to,frm in [('horz','hor_align'),('vert','vert_align'),('dire','text_direction'),('rota','rotation'),('wrap','text_wrapped'),('shri','shrink_to_fit'),('inde','indent_level')]:setattr(alignment,to,a[frm])
        b=source['border'];border=style.borders
        for edge in ('left','right','top','bottom'):
            setattr(border,edge,b[edge+'_line_style']);setattr(border,edge+'_colour',b[edge+'_colour_index'])
        border.diag=b['diag_line_style'];border.diag_colour=b['diag_colour_index'];border.need_diag1=b['diag_down'];border.need_diag2=b['diag_up']
        style.pattern.pattern=source['background']['fill_pattern'];style.pattern.pattern_fore_colour=source['background']['pattern_colour_index'];style.pattern.pattern_back_colour=source['background']['background_colour_index']
        style.protection.cell_locked=source['protection']['cell_locked'];style.protection.formula_hidden=source['protection']['formula_hidden'];result.append(style)
    return result

def build_workbook(info,items):
    if len(items)>65518:raise ValueError('测试项超出 XLS 最大行数，报告未截断，请检查测试序列。')
    root=Path(sys._MEIPASS) if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    layout=json.loads((root/'resources/test_report_layout.json').read_text(encoding='utf-8'))
    book=xlwt.Workbook(encoding='utf-8')
    normal=layout['fonts'][0]
    # BIFF Normal/column metrics use FONT record 0. xlwt's public default_style
    # points at record 6 instead; pinned xlwt 1.3.0 exposes no FONT-0 setter.
    normal_font=book._Workbook__styles._font_x2id[0]
    for key in ('name','height','colour_index','bold','italic','family'):
        setattr(book.default_style.font,key,normal[key])
        setattr(normal_font,key,normal[key])
    book.default_style.font.charset=normal['character_set']
    normal_font.charset=normal['character_set']
    for index,rgb in layout['palette'].items():
        if rgb is not None:book.set_colour_RGB(int(index),*rgb)
    styles=_styles(layout);sheet=book.add_sheet(layout['sheet_name'],cell_overwrite_ok=True)
    setup=layout['print_setup']
    for key in ('left_margin','right_margin','top_margin','bottom_margin','header_margin','footer_margin','paper_size_code','portrait'):setattr(sheet,key,setup[key])
    sheet.header_str=setup['header_str'].encode('utf-8');sheet.footer_str=setup['footer_str'].encode('utf-8')
    sheet.print_centered_horz=setup['horz_centered'];sheet.print_centered_vert=setup['vert_centered']
    colours={};display_styles={}
    def display_style(style_index,rgb):
        rgb=tuple(rgb)
        if rgb not in colours:
            index=24+len(colours);colours[rgb]=index;book.set_colour_RGB(index,*rgb)
        key=(style_index,rgb)
        if key not in display_styles:
            style=copy.deepcopy(styles[style_index]);style.pattern.pattern=1;style.pattern.pattern_fore_colour=colours[rgb];display_styles[key]=style
        return display_styles[key]
    def header_style(row,col):
        rgb=layout['display_fills'][row][col]
        if row==6 and col>=1:rgb=layout['result_pass_fill'] if str(info.get('result','')).upper()=='PASS' else layout['fail_fill']
        return display_style(layout['header_styles'][row][col],rgb)
    for col,width in enumerate(layout['widths']):sheet.col(col).width=width
    values=[list(row) for row in layout['labels']]
    for row,key in ((2,'sn'),(3,'date'),(4,'time'),(5,'duration'),(6,'result'),(8,'project'),(9,'operator'),(10,'model'),(11,'recipe_file'),(12,'position1'),(13,'position2'),(14,'equipment'),(15,'station')):values[row][1]=text(info.get(key,''))
    for row in range(18):
        sheet.row(row).height=layout['heights'][row];sheet.row(row).height_mismatch=bool(layout['height_mismatches'][row])
        for col in range(6):sheet.write(row,col,values[row][col],header_style(row,col))
    for r0,r1,c0,c1 in layout['merges']:
        sheet.write_merge(r0,r1-1,c0,c1-1,values[r0][c0],header_style(r0,c0))
    for row,item in enumerate(items,18):
        sheet.row(row).height=285;sheet.row(row).height_mismatch=False
        for col,key in enumerate(('item_name','measured_value','lower_limit','upper_limit','unit','result')):
            value=item.get(key)
            if key=='result':value={'PASS':'Pass','OK':'Pass','FAIL':'Fail','NG':'Fail'}.get(str(value).upper(),value)
            rgb=[255,255,255]
            if key=='result':rgb=layout['body_pass_fill'] if str(value).upper()=='PASS' else layout['fail_fill']
            sheet.write(row,col,text(value),display_style(layout['body_styles'][col],rgb))
    return book
