import os
from DrissionPage import ChromiumPage
from datetime import datetime

from DrissionPage.errors import ElementNotFoundError


class Crawler:
    def __init__(self, url: str, should_download_img: bool, tear_down: bool):
        self.url = url
        self.should_download_img = should_download_img
        self.tear_down = tear_down
        self.browser = ChromiumPage()

        task_id = datetime.now().strftime("%Y年%m月%d日%H时%M分%S秒")
        self.folder = f"./output/{task_id}"

    def __get(self):
        if "https://www.xiaohongshu.com/explore/" not in self.url:
            print("不合适的笔记链接，它不包含必要成分“https://www.xiaohongshu.com/explore/”")
            exit(1)
        self.browser.get(self.url)

    def __close_mask(self):
        tries = 0
        while tries < 3:
            try:
                self.browser.ele(".icon-btn-wrapper button close", timeout=2).click()
                return None
            except ElementNotFoundError:
                tries += 1
                print(f"没有找到关闭按钮，自动重试 {tries}")
        print("穷尽所有试错次数均没有找到关闭按钮，尝试忽略")
        return None


    def __obtain_title(self):
        return self.browser.ele("#detail-title").text

    def __obtain_content(self):
        return self.browser.ele("#detail-desc").ele(".note-text").child().text

    def __obtain_images(self):
        res = self.browser.ele(".swiper-wrapper").eles("@tag()=img")
        return list(set([x.attrs['src'] for x in res]))

    def run(self): # 哥们想要改就改这边的输出形式就好
        self.__get()
        self.__close_mask()
        os.makedirs(self.folder)
        title = self.__obtain_title()
        with open(file=f"{self.folder}/标题.txt", mode="w", encoding="utf-8") as f:
            f.write(title)
        content = self.__obtain_content()
        with open(file=f"{self.folder}/内容.txt", mode="w", encoding="utf-8") as f:
            f.write(content)

        if self.should_download_img:
            img_folder = f"{self.folder}/图片"
            os.makedirs(img_folder)
            img_urls = self.__obtain_images()
            for img_url in img_urls:
                self.browser.download(img_url, f"{img_folder}", suffix="webp")

        if self.tear_down:
            self.browser.quit()