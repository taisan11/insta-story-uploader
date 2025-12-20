import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from instagrapi import Client
from instagrapi.mixins import photo as ig_photo
from instagrapi.story import StoryBuilder
from instagrapi.types import StoryLink
from PIL import Image, ImageTk, ImageDraw
import threading
import os
from pathlib import Path
from instagrapi.exceptions import LoginRequired, ChallengeRequired, TwoFactorRequired
import logging
from typing import cast
from pydantic import HttpUrl
import tempfile

# Monkey patch: allow StoryBuilder(photo) that outputs MP4 to be routed to video upload
_orig_photo_upload_to_story = ig_photo.UploadPhotoMixin.photo_upload_to_story


def _patched_photo_upload_to_story(
    self,
    path: Path,
    caption: str = "",
    upload_id: str = "",
    mentions=None,
    locations=None,
    links=None,
    hashtags=None,
    stickers=None,
    medias=None,
    polls=None,
    extra_data=None,
):
    # Normalize optional lists to avoid mutable default pitfalls
    mentions = mentions or []
    locations = locations or []
    links = links or []
    hashtags = hashtags or []
    stickers = stickers or []
    medias = medias or []
    polls = polls or []
    extra_data = extra_data or {}

    file_path = Path(path)
    # If StoryBuilder produced an MP4 (e.g., due to processing), delegate to video upload
    if file_path.suffix.lower() == ".mp4":
        return self.video_upload_to_story(
            file_path,
            caption=caption,
            mentions=mentions,
            locations=locations,
            links=links,
            hashtags=hashtags,
            stickers=stickers,
            medias=medias,
            polls=polls,
            extra_data=extra_data,
        )

    return _orig_photo_upload_to_story(
        self,
        file_path,
        caption=caption,
        upload_id=upload_id,
        mentions=mentions,
        locations=locations,
        links=links,
        hashtags=hashtags,
        stickers=stickers,
        medias=medias,
        polls=polls,
        extra_data=extra_data,
    )


# Apply monkey patch
ig_photo.UploadPhotoMixin.photo_upload_to_story = _patched_photo_upload_to_story

class StoryUploader:
    def __init__(self, root):
        self.root = root
        self.root.title("Instagram Story Uploader")
        self.root.geometry("600x800")
        
        self.cl = Client()
        self.cl.delay_range = [1, 4]
        self.logged_in = False
        self.selected_file_path = None
        self.status_text = "ログインしてください"
        self.default_link_geom = {
            "x": 0.5126011,
            "y": 0.5168225,
            "w": 0.50998676,
            "h": 0.25875,
        }
        self.link_rows = []
        self.preview_max_size = (320, 220)
        resampling = getattr(Image, "Resampling", Image)
        self.resample_filter = getattr(resampling, "LANCZOS", getattr(resampling, "BICUBIC", getattr(resampling, "NEAREST", 0)))
        
        # UI 構築後にセッションを読み込み、表示を更新
        self.setup_ui()
        self.load_session()
        if hasattr(self, "status_label"):
            self.status_label.config(
                text=self.status_text,
                fg="green" if self.logged_in else "blue",
            )

    # Link Sticker UI helpers
    def _create_link_row(self, index: int):
        row = tk.Frame(self.link_rows_frame)
        row.pack(fill=tk.X, pady=4)

        top = tk.Frame(row)
        top.pack(fill=tk.X)

        tk.Label(top, text=f"Link {index}", width=6, anchor=tk.W, font=("Arial", 8)).pack(side=tk.LEFT)

        url_entry = tk.Entry(top, width=28)
        url_entry.insert(0, "https://")
        url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        icon_var = tk.StringVar(value="")

        def choose_icon():
            file_path = filedialog.askopenfilename(
                title="Link用画像を選択",
                filetypes=[
                    ("画像ファイル", "*.png *.jpg *.jpeg *.webp"),
                    ("PNG", "*.png"),
                    ("JPEG", "*.jpg *.jpeg"),
                    ("WEBP", "*.webp"),
                ],
            )
            if file_path:
                icon_var.set(file_path)
                icon_label.config(text=os.path.basename(file_path))
                self.refresh_preview()

        icon_btn = tk.Button(top, text="画像選択", command=choose_icon)
        icon_btn.pack(side=tk.LEFT, padx=(4, 4))
        icon_label = tk.Label(top, text="(なし)", width=10, anchor=tk.W, font=("Arial", 8))
        icon_label.pack(side=tk.LEFT)

        bottom = tk.Frame(row)
        bottom.pack(fill=tk.X, pady=(2, 0))

        def add_field(label_text: str, key: str, width: int = 6):
            wrapper = tk.Frame(bottom)
            wrapper.pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(wrapper, text=label_text, font=("Arial", 8)).pack(anchor=tk.W)
            entry = tk.Entry(wrapper, width=width)
            entry.insert(0, str(self.default_link_geom[key]))
            entry.pack()
            return entry

        x_entry = add_field("X", "x")
        y_entry = add_field("Y", "y")

        w_var = tk.DoubleVar(value=self.default_link_geom["w"])
        h_var = tk.DoubleVar(value=self.default_link_geom["h"])

        def on_scale_change(var, entry):
            entry.delete(0, tk.END)
            entry.insert(0, f"{var.get():.4f}")
            self.refresh_preview()

        def bind_entry(entry, var):
            def _on_change(_event=None):
                try:
                    val = float(entry.get())
                    var.set(val)
                except ValueError:
                    pass
                self.refresh_preview()
            entry.bind("<KeyRelease>", _on_change)
            entry.bind("<FocusOut>", _on_change)

        w_entry = add_field("幅", "w", width=7)
        h_entry = add_field("高さ", "h", width=7)
        bind_entry(w_entry, w_var)
        bind_entry(h_entry, h_var)

        w_scale = tk.Scale(bottom, from_=0.05, to=1.0, orient=tk.HORIZONTAL, resolution=0.01,
                           variable=w_var, length=110, command=lambda _v: on_scale_change(w_var, w_entry))
        w_scale.pack(side=tk.LEFT, padx=(0, 6))

        h_scale = tk.Scale(bottom, from_=0.05, to=1.0, orient=tk.HORIZONTAL, resolution=0.01,
                           variable=h_var, length=110, command=lambda _v: on_scale_change(h_var, h_entry))
        h_scale.pack(side=tk.LEFT, padx=(0, 6))

        def bind_url_refresh(entry_widget):
            def _cb(_event=None):
                self.refresh_preview()
            entry_widget.bind("<KeyRelease>", _cb)
            entry_widget.bind("<FocusOut>", _cb)

        bind_url_refresh(url_entry)
        bind_url_refresh(x_entry)
        bind_url_refresh(y_entry)

        return {
            "frame": row,
            "url": url_entry,
            "x": x_entry,
            "y": y_entry,
            "w": w_entry,
            "h": h_entry,
            "w_var": w_var,
            "h_var": h_var,
            "w_scale": w_scale,
            "h_scale": h_scale,
            "icon_var": icon_var,
            "icon_label": icon_label,
        }

    def add_link_row(self):
        row = self._create_link_row(len(self.link_rows) + 1)
        self.link_rows.append(row)
        self.refresh_preview()

    def remove_link_row(self):
        # 常に1行は残す
        if len(self.link_rows) <= 1:
            return
        last = self.link_rows.pop()
        last["frame"].destroy()
        self.refresh_preview()

    def refresh_preview(self):
        if self.selected_file_path:
            self.show_preview(self.selected_file_path)
    
    def load_session(self):
        """保存されたセッションの読み込みを試行"""
        logger = logging.getLogger()
        session_path = Path("session.json")
        self.status_text = "ログインしてください"
        self.logged_in = False

        if session_path.exists():
            try:
                session = self.cl.load_settings(session_path)
                if session:
                    self.cl.set_settings(session)
                    try:
                        # セッションが有効か確認
                        self.cl.get_timeline_feed()
                        user_info = self.cl.account_info()
                        self.logged_in = True
                        self.status_text = f"ログイン済み: {user_info.username}"
                        return
                    except LoginRequired:
                        logger.info("Session is invalid, need to login")
                        old_session = self.cl.get_settings()
                        self.cl.set_settings({})
                        self.cl.set_uuids(old_session.get("uuids", {}))
                        self.status_text = "セッションが無効です。再ログインしてください"
                        self.logged_in = False
                        return
            except Exception as e:
                logger.info(f"Couldn't login using session: {e}")
                self.status_text = "セッション復元失敗。ログインしてください"
                self.logged_in = False
    
    def setup_ui(self):
        # メニューバー
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        account_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="アカウント", menu=account_menu)
        account_menu.add_command(label="ログイン", command=self.login_popup)
        account_menu.add_command(label="ログアウト", command=self.logout)
        
        # メインフレーム
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ステータス表示
        self.status_label = tk.Label(main_frame, text=self.status_text, fg="blue", font=("Arial", 10))
        self.status_label.pack(pady=(0, 0))
        
        # ファイル選択エリア
        file_frame = tk.LabelFrame(main_frame, text="ファイル選択", padx=10, pady=10)
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.file_label = tk.Label(file_frame, text="ファイル未選択", fg="gray")
        self.file_label.pack(side=tk.LEFT, padx=(0, 10))
        
        select_btn = tk.Button(file_frame, text="ファイルを選択", command=self.select_file)
        select_btn.pack(side=tk.LEFT)
        
        # プレビューエリア
        preview_frame = tk.LabelFrame(main_frame, text="プレビュー", padx=10, pady=10, height=240)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        preview_frame.pack_propagate(False)
        
        self.preview_label = tk.Label(preview_frame, text="画像/動画が選択されていません", bg="lightgray")
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        
        # Link Sticker入力
        link_frame = tk.LabelFrame(main_frame, text="Link Sticker (オプション)", padx=10, pady=10)
        link_frame.pack(fill=tk.X, pady=(0, 10))
        
        link_info_label = tk.Label(link_frame, text="ストーリーに添付するリンク", fg="gray", font=("Arial", 8))
        link_info_label.pack(anchor=tk.W, pady=(0, 5))
        
        self.link_rows_frame = tk.Frame(link_frame)
        self.link_rows_frame.pack(fill=tk.X, pady=(0, 5))

        controls = tk.Frame(link_frame)
        controls.pack(anchor=tk.W, pady=(0, 5))
        tk.Button(controls, text="＋", width=3, command=self.add_link_row).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(controls, text="－", width=3, command=self.remove_link_row).pack(side=tk.LEFT)
        tk.Button(controls, text="プレビュー更新", command=self.refresh_preview).pack(side=tk.LEFT, padx=(8, 0))

        # デフォルトで 1 行表示
        self.add_link_row()
        
        # アップロードボタン
        upload_btn = tk.Button(main_frame, text="ストーリーをアップロード", 
                              command=self.upload_story, bg="#0095f6", fg="white",
                              font=("Arial", 12, "bold"), height=2)
        upload_btn.pack(fill=tk.X)
    
    def login_popup(self):
        """ログインダイアログを表示"""
        dialog = tk.Toplevel(self.root)
        dialog.title("ログイン")
        dialog.geometry("300x150")
        dialog.transient(self.root)
        dialog.grab_set()
        
        tk.Label(dialog, text="ユーザー名:").pack(pady=(10, 0))
        username_entry = tk.Entry(dialog)
        username_entry.pack(pady=(0, 10))
        
        tk.Label(dialog, text="パスワード:").pack()
        password_entry = tk.Entry(dialog, show="*")
        password_entry.pack(pady=(0, 10))
        
        def do_login():
            username = username_entry.get()
            password = password_entry.get()
            
            if not username or not password:
                messagebox.showerror("エラー", "ユーザー名とパスワードを入力してください")
                return
            
            dialog.destroy()
            self.login(username, password)
        
        tk.Button(dialog, text="ログイン", command=do_login).pack()
    
    def login(self, username, password):
        """Instagramにログイン"""
        def login_thread():
            try:
                self.status_label.config(text="ログイン中...")
                self.cl.login(username, password)
                
                # セッションを保存
                self.cl.dump_settings(Path("session.json"))
                
                user_info = self.cl.account_info()
                self.logged_in = True
                self.status_label.config(text=f"ログイン成功: {user_info.username}", fg="green")
                messagebox.showinfo("成功", f"{user_info.username}としてログインしました")
                
            except TwoFactorRequired:
                self.root.after(0, lambda: self.handle_2fa(username, password))
            except ChallengeRequired:
                messagebox.showerror("エラー", "チャレンジが必要です。ブラウザでログインしてください")
                self.status_label.config(text="ログイン失敗", fg="red")
            except Exception as e:
                messagebox.showerror("エラー", f"ログイン失敗: {str(e)}")
                self.status_label.config(text="ログイン失敗", fg="red")
        
        threading.Thread(target=login_thread, daemon=True).start()
    
    def handle_2fa(self, username, password):
        """2要素認証の処理"""
        code = simpledialog.askstring("2要素認証", "認証コードを入力してください:")
        if code:
            try:
                self.cl.login(username, password, verification_code=code)
                self.cl.dump_settings(Path("session.json"))
                user_info = self.cl.account_info()
                self.logged_in = True
                self.status_label.config(text=f"ログイン成功: {user_info.username}", fg="green")
                messagebox.showinfo("成功", f"{user_info.username}としてログインしました")
            except Exception as e:
                messagebox.showerror("エラー", f"2要素認証失敗: {str(e)}")
                self.status_label.config(text="ログイン失敗", fg="red")
    
    def logout(self):
        """ログアウト"""
        if messagebox.askyesno("確認", "ログアウトしますか?"):
            self.cl.logout()
            self.logged_in = False
            session_path = Path("session.json")
            if session_path.exists():
                session_path.unlink()
            self.status_label.config(text="ログアウトしました", fg="blue")
            messagebox.showinfo("成功", "ログアウトしました")
    
    def select_file(self):
        """ファイル選択ダイアログ"""
        file_path = filedialog.askopenfilename(
            title="ストーリーファイルを選択",
            filetypes=[
                ("画像・動画ファイル", "*.jpg *.jpeg *.png *.mp4"),
                ("JPEGファイル", "*.jpg *.jpeg"),
                ("MP4ファイル", "*.mp4"),
                ("すべてのファイル", "*.*")
            ]
        )
        
        if file_path:
            self.selected_file_path = file_path
            file_name = os.path.basename(file_path)
            self.file_label.config(text=file_name, fg="black")
            
            # プレビューを表示
            self.show_preview(file_path)
    
    def show_preview(self, file_path):
        """選択したファイルのプレビューを表示"""
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext in ['.jpg', '.jpeg', '.png']:
            try:
                image = Image.open(file_path)
                orig_w, orig_h = image.size
                preview = image.copy()
                preview.thumbnail(self.preview_max_size)

                # 描画: Link Sticker 当たり判定（URLが入力されている行のみ）
                draw = ImageDraw.Draw(preview)
                preview_w, preview_h = preview.size
                for row in self.link_rows:
                    url = row["url"].get().strip()
                    if not url or url == "https://":
                        continue
                    try:
                        link_x = float(row["x"].get() or self.default_link_geom["x"])
                        link_y = float(row["y"].get() or self.default_link_geom["y"])
                        link_w = float(row["w"].get() or self.default_link_geom["w"])
                        link_h = float(row["h"].get() or self.default_link_geom["h"])
                    except ValueError:
                        continue
                    px_w = link_w * preview_w
                    px_h = link_h * preview_h
                    cx = link_x * preview_w
                    cy = link_y * preview_h
                    bbox = [
                        cx - px_w / 2,
                        cy - px_h / 2,
                        cx + px_w / 2,
                        cy + px_h / 2,
                    ]
                    draw.rectangle(bbox, outline="red", width=2)

                photo = ImageTk.PhotoImage(preview)
                self.preview_label.config(image=photo, text="")
                self.preview_label.image = photo  # type: ignore
            except Exception as e:
                self.preview_label.config(text=f"画像読み込みエラー: {str(e)}")
        elif ext == '.mp4':
            self.preview_label.config(text=f"動画ファイル\n{os.path.basename(file_path)}")
        else:
            self.preview_label.config(text="未対応のファイル形式")
    
    def upload_story(self):
        """ストーリーをアップロード"""
        if not self.logged_in:
            messagebox.showerror("エラー", "先にログインしてください")
            return
        
        if not self.selected_file_path:
            messagebox.showerror("エラー", "ファイルを選択してください")
            return
        
        def collect_links_with_icons():
            links = []
            overlays = []
            for idx, row in enumerate(self.link_rows, start=1):
                url = row["url"].get().strip()
                if not url or url == "https://":
                    continue
                try:
                    link_x = float(row["x"].get() or self.default_link_geom["x"])
                    link_y = float(row["y"].get() or self.default_link_geom["y"])
                    link_w = float(row["w"].get() or self.default_link_geom["w"])
                    link_h = float(row["h"].get() or self.default_link_geom["h"])
                except ValueError:
                    messagebox.showerror("エラー", f"Link {idx} の位置とサイズは数値で入力してください")
                    return None
                links.append(
                    StoryLink(
                        webUri=cast(HttpUrl, url),
                        x=link_x,
                        y=link_y,
                        width=link_w,
                        height=link_h,
                    )
                )
                overlays.append({
                    "icon_path": row["icon_var"].get().strip(),
                    "geom": (link_x, link_y, link_w, link_h),
                })
            return links, overlays
        
        def upload_thread():
            try:
                self.status_label.config(text="アップロード中...")
                
                if not self.selected_file_path:
                    return
                
                file_path = Path(self.selected_file_path)
                ext = os.path.splitext(str(file_path))[1].lower()
                
                link_result = collect_links_with_icons()
                if link_result is None:
                    return
                links, overlays = link_result
                
                # StoryBuilderを使用してストーリーを構築
                temp_upload_path = None
                if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    # 画像に Link 用アイコンを合成（アイコン指定がある行のみ）
                    composite_needed = any(item.get("icon_path") for item in overlays)
                    target_path = file_path
                    if composite_needed:
                        try:
                            with Image.open(file_path).convert("RGBA") as base:
                                base_w, base_h = base.size
                                canvas = base.copy()
                                for item in overlays:
                                    icon_path = item.get("icon_path")
                                    if not icon_path:
                                        continue
                                    geom = item.get("geom") or (
                                        self.default_link_geom["x"],
                                        self.default_link_geom["y"],
                                        self.default_link_geom["w"],
                                        self.default_link_geom["h"],
                                    )
                                    link_x = float(geom[0])
                                    link_y = float(geom[1])
                                    link_w = float(geom[2])
                                    link_h = float(geom[3])
                                    try:
                                        with Image.open(icon_path).convert("RGBA") as icon_img:
                                            target_w = max(1, int(link_w * base_w))
                                            target_h = max(1, int(link_h * base_h))
                                            icon_resized = icon_img.resize((target_w, target_h), self.resample_filter)
                                            cx = link_x * base_w
                                            cy = link_y * base_h
                                            paste_x = int(cx - target_w / 2)
                                            paste_y = int(cy - target_h / 2)
                                            canvas.alpha_composite(icon_resized, (paste_x, paste_y))
                                    except Exception as e:
                                        print(f"アイコン合成に失敗: {e}")
                                        continue

                                # 形式は元画像に合わせる（JPEGの場合はRGBに変換）
                                suffix = file_path.suffix.lower()
                                if suffix in [".jpg", ".jpeg"]:
                                    canvas = canvas.convert("RGB")
                                    suffix = ".jpg"
                                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                                    canvas.save(tmp.name)
                                    target_path = Path(tmp.name)
                                    temp_upload_path = target_path

                        except Exception as e:
                            print(f"合成処理に失敗: {e}")

                    story = StoryBuilder(target_path).photo()
                    self.cl.photo_upload_to_story(
                        story.path,
                        links=links
                    )
                elif ext == '.mp4':
                    # 動画ストーリー（必要ならアイコンを焼き込み）
                    target_video_path = file_path
                    composite_needed = any(item.get("icon_path") for item in overlays)
                    if composite_needed:
                        try:
                            try:
                                from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip
                                from moviepy.video.fx.resize import resize as mp_resize
                            except ImportError:
                                messagebox.showerror("エラー", "moviepy が必要です。`uv pip install moviepy` を実行してください。")
                                return

                            base_clip = VideoFileClip(str(file_path))
                            width, height = base_clip.size
                            overlay_clips = []
                            for item in overlays:
                                icon_path = item.get("icon_path")
                                if not icon_path:
                                    continue
                                geom = item.get("geom") or (
                                    self.default_link_geom["x"],
                                    self.default_link_geom["y"],
                                    self.default_link_geom["w"],
                                    self.default_link_geom["h"],
                                )
                                link_x = float(geom[0])
                                link_y = float(geom[1])
                                link_w = float(geom[2])
                                link_h = float(geom[3])
                                target_w = max(1, int(link_w * width))
                                target_h = max(1, int(link_h * height))
                                pos_x = link_x * width - target_w / 2
                                pos_y = link_y * height - target_h / 2
                                try:
                                    icon_clip = ImageClip(icon_path)
                                    icon_clip = mp_resize(icon_clip, newsize=(target_w, target_h))
                                    icon_clip = icon_clip.set_duration(base_clip.duration).set_pos((pos_x, pos_y))
                                    overlay_clips.append(icon_clip)
                                except Exception as e:
                                    print(f"動画アイコン合成に失敗: {e}")
                                    continue

                            if overlay_clips:
                                composite = CompositeVideoClip([base_clip, *overlay_clips])
                                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
                                    temp_upload_path = Path(tmp.name)
                                audio_path = temp_upload_path.with_suffix(".m4a")
                                composite.write_videofile(
                                    str(temp_upload_path),
                                    codec="libx264",
                                    audio_codec="aac",
                                    temp_audiofile=str(audio_path),
                                    remove_temp=True,
                                    verbose=False,
                                    logger=None,
                                )
                                target_video_path = temp_upload_path
                                composite.close()
                                base_clip.close()
                                for clip in overlay_clips:
                                    try:
                                        clip.close()
                                    except Exception:
                                        pass
                            else:
                                base_clip.close()
                        except Exception as e:
                            print(f"動画へのアイコン合成に失敗: {e}")

                    story = StoryBuilder(target_video_path).video()
                    self.cl.video_upload_to_story(
                        story.path,
                        links=links
                    )
                else:
                    raise ValueError("未対応のファイル形式です")
                if temp_upload_path:
                    try:
                        temp_upload_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                
                self.status_label.config(text="アップロード成功!", fg="green")
                messagebox.showinfo("成功", "ストーリーをアップロードしました!")
                
                # フィールドをクリア
                self.selected_file_path = None
                self.file_label.config(text="ファイル未選択", fg="gray")
                self.preview_label.config(image="", text="画像/動画が選択されていません")
                for row in self.link_rows:
                    row["url"].delete(0, tk.END)
                    row["url"].insert(0, "https://")
                    row["x"].delete(0, tk.END)
                    row["x"].insert(0, str(self.default_link_geom["x"]))
                    row["y"].delete(0, tk.END)
                    row["y"].insert(0, str(self.default_link_geom["y"]))
                    row["w"].delete(0, tk.END)
                    row["w"].insert(0, str(self.default_link_geom["w"]))
                    row["h"].delete(0, tk.END)
                    row["h"].insert(0, str(self.default_link_geom["h"]))
                    row["w_var"].set(self.default_link_geom["w"])
                    row["h_var"].set(self.default_link_geom["h"])
                    row["icon_var"].set("")
                    row["icon_label"].config(text="(なし)")
                
            except Exception as e:
                print(e)
                self.status_label.config(text="アップロード失敗", fg="red")
                messagebox.showerror("エラー", f"アップロード失敗: {str(e)}")
        
        threading.Thread(target=upload_thread, daemon=True).start()


def main():
    root = tk.Tk()
    app = StoryUploader(root)
    root.mainloop()


if __name__ == "__main__":
    main()

