from Crawler import Crawler

url = input("请输入链接：")
download_img = input("是否保存图片[Y]/n：").strip().upper() != "N"
tear_down = input("是否自动关闭浏览器[Y]/n：").strip().upper() != "N"

Crawler(url, download_img, tear_down).run()
