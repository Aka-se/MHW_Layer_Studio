"""Choose protected model units without changing manual layer assignments."""
import tkinter as tk
from tkinter import ttk
from damage_model import units
from dialog_geometry import center_dialog
from ui_tables import ResizableTreeview


class DamageSettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title('随机战损 · 基础模型保护')
        self.geometry('820x560')
        self.minsize(640, 420)
        self.transient(app)
        self.grab_set()
        self.roles = {key: value.get('damage_role', '') for key, value in app.session.annotations.items()}
        self.records = units(app.session)
        ttk.Label(self, padding=10, wraplength=740, text=(
            '每下降5个百分点，按比例随机隐藏；回血恢复对应数量，再掉血重新抽取。\n'
            '已有子组作为完整单位，父组不合并子组；组内任一基础成员都会保护整组。\n'
            'Group 0 默认受保护。标记只影响随机模式，原手动层级保留。')).pack(fill='x')
        frame = ttk.Frame(self, padding=(10, 0))
        frame.pack(fill='both', expand=True)
        self.tree = ResizableTreeview(frame, columns=('part', 'count', 'role'), show='tree headings', selectmode='extended')
        for column, title, width in (('#0', '部件组 / Mesh', 330), ('part', '套装', 180), ('count', 'Mesh数', 70), ('role', '随机模式', 130)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=50, stretch=True)
        scroll = ttk.Scrollbar(frame, command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        row = ttk.Frame(self, padding=10)
        row.pack(fill='x')
        for text, role in (('设为基础 · 永不隐藏', 'base'), ('参与随机', 'random'), ('恢复默认', '')):
            ttk.Button(row, text=text, command=lambda r=role: self.mark(r)).pack(side='left', padx=(0, 6))
        ttk.Button(row, text='其余参与随机', command=self.mark_others).pack(side='left')
        self.summary = ttk.Label(self, padding=(10, 0), style='Hint.TLabel')
        self.summary.pack(fill='x')
        ttk.Label(self, padding=10, wraplength=740, style='Hint.TLabel', text=(
            '隐藏数量按每套装的可随机组数计算并向下取整；组少时，多个5%阶段才减少一组。\n'
            '预览与游戏各自随机。导出后请保留 nativePC/plugins 内的 MHWLayerStudio 配置，使用新版扩展插件。')).pack(fill='x')
        footer = ttk.Frame(self, padding=10)
        footer.pack(fill='x')
        ttk.Button(footer, text='应用', command=self.apply).pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=6)
        self.bind('<Escape>', lambda _: self.destroy())
        self.refresh()
        center_dialog(self, app)

    def protected(self, record):
        return any(self.roles.get(k) == 'base' or (not self.roles.get(k) and
                   self.app.session.document(k[0]).meshes[k[1]].visible_condition == 0)
                   for k in record['keys'] if k not in record.get('hidden_keys', ()))

    def refresh(self):
        for i, r in enumerate(self.records):
            hidden = len(r.get('hidden_keys', ()))
            role = '始终隐藏' if hidden == len(r['keys']) else '基础 · 保护' if self.protected(r) else '参与随机'
            if 0 < hidden < len(r['keys']):
                role += f' · {hidden}个始终隐藏'
            values = (r['pool'], len(r['keys']), role)
            if self.tree.exists(str(i)):
                self.tree.item(str(i), values=values)
            else:
                self.tree.insert('', 'end', iid=str(i), text=r['name'], values=values)
        active = [r for r in self.records if set(r['keys']) - set(r.get('hidden_keys', ()))]
        protected = sum(self.protected(r) for r in active)
        self.summary.configure(text=f'共 {len(self.records)} 个单位 · {len(self.records) - len(active)} 个全部始终隐藏 · {protected} 个受保护 · {len(active) - protected} 个参与随机')

    def mark(self, role):
        for iid in self.tree.selection():
            for key in self.records[int(iid)]['keys']:
                self.roles[key] = role
        self.refresh()

    def mark_others(self):
        # First explicitly mark the body/skin as base, then opt all other units in.
        for record in self.records:
            if not any(self.roles.get(key) == 'base' for key in record['keys']):
                for key in record['keys']:
                    self.roles[key] = 'random'
        self.refresh()

    def apply(self):
        # One undo for all staged role changes.
        session = self.app.session
        before = session._annotation_snapshot()
        for r in self.records:
            for key in r['keys']:
                role = self.roles.get(key, '')
                if role:
                    session.annotations.setdefault(key, {'name': '', 'note': ''})['damage_role'] = role
                elif key in session.annotations:
                    session.annotations[key].pop('damage_role', None)
        if session.annotations != before:
            session._undo.append({'kind': 'annotations', 'before': before, 'after': session._annotation_snapshot()})
            session._redo.clear()
        self.app._refresh_groups()
        self.app._update_damage_preview()
        self.app.schedule_redraw()
        self.app.status.set('随机战损保护设置已更新 · 手动层级保留 · 请导出或覆盖保存')
        self.destroy()
