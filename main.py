import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from instagrapi import Client
from instagrapi.types import StoryLink
from PIL import Image, ImageTk
import threading
import os
from pathlib import Path
from instagrapi.exceptions import LoginRequired, ChallengeRequired, TwoFactorRequired
import logging
from typing import cast
from pydantic import HttpUrl

class StoryUploader:
    def __init__(self, root):
        self.root = root
        self.root.title("Instagram Story Uploader")
        self.root.geometry("600x500")
        
        self.cl = Client()
        self.cl.delay_range = [1, 4]
        self.logged_in = False
        self.selected_file_path = None
        
        # セッションファイルの読み込みを試行
        self.load_session()
        
        self.setup_ui()
    
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
        self.status_label.pack(pady=(0, 20))
        
        # ファイル選択エリア
        file_frame = tk.LabelFrame(main_frame, text="ファイル選択", padx=10, pady=10)
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.file_label = tk.Label(file_frame, text="ファイル未選択", fg="gray")
        self.file_label.pack(side=tk.LEFT, padx=(0, 10))
        
        select_btn = tk.Button(file_frame, text="ファイルを選択", command=self.select_file)
        select_btn.pack(side=tk.LEFT)
        
        # プレビューエリア
        preview_frame = tk.LabelFrame(main_frame, text="プレビュー", padx=10, pady=10)
        preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        self.preview_label = tk.Label(preview_frame, text="画像/動画が選択されていません", bg="lightgray")
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        
        # キャプション入力
        caption_frame = tk.LabelFrame(main_frame, text="キャプション", padx=10, pady=10)
        caption_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.caption_text = tk.Text(caption_frame, height=3, wrap=tk.WORD)
        self.caption_text.pack(fill=tk.X)
        
        # リンク入力
        link_frame = tk.LabelFrame(main_frame, text="リンク (オプション)", padx=10, pady=10)
        link_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.link_entry = tk.Entry(link_frame)
        self.link_entry.pack(fill=tk.X)
        self.link_entry.insert(0, "https://")
        
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
                # プレビューサイズに調整
                image.thumbnail((200, 200))
                photo = ImageTk.PhotoImage(image)
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
        
        caption = self.caption_text.get("1.0", tk.END).strip()
        link = self.link_entry.get().strip()
        if link == "https://":
            link = ""
        
        def upload_thread():
            try:
                self.status_label.config(text="アップロード中...")
                
                if not self.selected_file_path:
                    return
                
                file_path = Path(self.selected_file_path)
                ext = os.path.splitext(str(file_path))[1].lower()
                
                if ext in ['.jpg', '.jpeg', '.png']:
                    # 画像ストーリー
                    links = [StoryLink(webUri=cast(HttpUrl, link))] if link else []
                    self.cl.photo_upload_to_story(
                        file_path,
                        caption=caption,
                        links=links
                    )
                elif ext == '.mp4':
                    # 動画ストーリー
                    links = [StoryLink(webUri=cast(HttpUrl, link))] if link else []
                    self.cl.video_upload_to_story(
                        file_path,
                        caption=caption,
                        links=links
                    )
                else:
                    raise ValueError("未対応のファイル形式です")
                
                self.status_label.config(text="アップロード成功!", fg="green")
                messagebox.showinfo("成功", "ストーリーをアップロードしました!")
                
                # フィールドをクリア
                self.selected_file_path = None
                self.file_label.config(text="ファイル未選択", fg="gray")
                self.preview_label.config(image="", text="画像/動画が選択されていません")
                self.caption_text.delete("1.0", tk.END)
                self.link_entry.delete(0, tk.END)
                self.link_entry.insert(0, "https://")
                
            except Exception as e:
                self.status_label.config(text="アップロード失敗", fg="red")
                messagebox.showerror("エラー", f"アップロード失敗: {str(e)}")
        
        threading.Thread(target=upload_thread, daemon=True).start()


def main():
    root = tk.Tk()
    app = StoryUploader(root)
    root.mainloop()


if __name__ == "__main__":
    main()

