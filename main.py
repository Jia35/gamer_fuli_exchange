import time
import queue
import threading
import configparser

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')


def save_cookie():
    """登入巴哈網站，儲存 cookie"""
    userid = config['login']['userid']
    password = config['login']['password']
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=config.getboolean('settings', 'is_headless')  # 是否開啟無頭模式
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://user.gamer.com.tw/login.php")
        try:
            page.fill('#form-login [name=userid]', userid)
            page.fill('#form-login [name=password]', password)
            page.click('#btn-login')
            page.wait_for_timeout(3000)
            # TODO: 確認是否登入成功
            context.storage_state(path="login_cookie.json")
        except:
            print('登入失敗')
        browser.close()


def get_goods_url():
    """取得可透過觀看廣告兌換的商品網址"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.92 Safari/537.36',
    }
    fuli_url = 'https://fuli.gamer.com.tw/'

    goods_list = []
    for i in range(3):
        r = requests.get(f'{fuli_url}?page={i+1}', headers=headers)
        if r.status_code != requests.codes.ok:
            print('網頁抓取失敗:', r.status_code)
        soup = BeautifulSoup(r.text, 'html.parser')
        items_element = soup.select('.item-list-box .items-card')

        for item_element in items_element:
            if item_element.select_one('.type-tag').text.strip() != '抽抽樂':
                continue
            url = item_element.get('href')
            name = item_element.select_one('h2').text.strip()
            price = item_element.select_one('.price .digital').text.strip()
            goods = {
                'name': name,
                'price': price,
                'url': url
            }
            if goods not in goods_list:
                goods_list.append(goods)

    return goods_list


class exchangeGoodsThread(threading.Thread):
    def __init__(self, index, url_queue, error_queue):
        threading.Thread.__init__(self)

        if url_queue.qsize() == 0:
            print(f'結束：{index}')
            return
        self.index = index
        self.url_queue = url_queue
        self.error_queue = error_queue
        self.browser = None
        self.is_headless = config.getboolean('settings', 'is_headless')  # 是否開啟無頭模式
        self.page = None
        self.frame = None
        self.url = None

    def run(self):
        """觀看廣告，兌換商品"""
        with sync_playwright() as playwright:
            self.create_browser(playwright)

            while self.url_queue.qsize() > 0:
                # 取得商品的 URL
                self.url = self.url_queue.get()
                # 前往商品頁面
                self.page.goto(self.url)
                if not self.is_login():
                    self.browser.close()
                    return
                self.page.wait_for_timeout(2000)

                # 重複觀看此商品廣告
                for i in range(int(config['settings']['watch_num'])):
                    print(f'{self.index}：第 {i+1} 次執行：{self.url}')
                    # 點擊前往"觀看廣告"
                    need_break = self.click_watch_ad(timeout=15)
                    if need_break:
                        break

                    self.page.wait_for_timeout(5000)
                    # 判斷是否已經自動跳到抽獎
                    if 'buyD' not in self.page.url:
                        # 點擊"確認觀看廣告"
                        need_break = self.click_confirm_watch_ad(timeout=10)
                        if need_break:
                            break
                        # 切換到廣告視窗 iframe 內
                        need_break = self.switch_to_ad_iframe(timeout=10)
                        if need_break:
                            break

                        self.page.wait_for_timeout(5000)
                        # 如果出現"繼續有聲播放"按鈕需點擊後繼續
                        self.click_continue_watch_ad(timeout=20)

                        self.page.wait_for_timeout(18000)
                        # 影片"播放完畢"或"可跳過"，則關閉影片
                        need_break = self.close_ad_iframe(timeout=10)
                        if need_break:
                            break

                    # self.page.wait_for_timeout(3000)
                    # 看完廣告，送出抽獎資料
                    need_break = self.send_lottery_info(timeout=15)
                    if need_break:
                        break

                    # 點擊'確認兌換商品'彈跳視窗
                    need_break = self.click_continue_exchange_goods(timeout=10)
                    if need_break:
                        break

                    # 看完廣告、送出資料，返回商品頁
                    self.page.goto(self.url)

            print(f'--- 結束：{self.index} ---')
            self.browser.close()

    def create_browser(self, playwright):
        """創建瀏覽器，並載入已登入的 cookie"""
        self.browser = playwright.chromium.launch(
            args=["--mute-audio"],  # 靜音
            channel="chrome",
            headless=self.is_headless,  # 是否開啟無頭模式
        )
        context = self.browser.new_context(storage_state="login_cookie.json")
        self.page = context.new_page()
        # self.page.set_viewport_size({"width": 640, "height": 480})

    def is_login(self, timeout=10):
        """是否已登入"""
        try:
            self.page.wait_for_selector('.topbar_member-home', timeout=timeout*1000)
        except Exception:
            print('登入失敗，退出')
            self.error_queue.put([self.url, '登入失敗'])
            return False
        return True

    def click_watch_ad(self, timeout=10):
        """點擊前往'觀看廣告'"""
        need_break = False
        try:
            element = self.page.wait_for_selector('a.c-accent-o', timeout=timeout*1000)
            if 'is-disable' in element.get_attribute("class"):
                print(f'{self.index}：本日免費兌換次數已用盡')
                need_break = True
            else:
                element.click()
        except Exception as e:
            print(f'{self.index}：找不到"看廣告免費兌換"按鈕')
            self.error_queue.put([self.url, '找不到"看廣告免費兌換"按鈕'])
            need_break = True
        return need_break

    def click_confirm_watch_ad(self, timeout=10):
        """點擊'確認觀看廣告'"""
        # 判斷順序：確認觀看廣告 > 新商品答題 > 廣告能量補充中
        need_break = False
        try:
            self.page.click('#dialogify_3 form [type="submit"]', timeout=timeout*1000)
        except Exception:
            try:
                self.page.click('#dialogify_2 form [type="submit"]', timeout=1000)
            except Exception:
                try:
                    self.page.click('#dialogify form [type="submit"]', timeout=1000)
                except Exception:

                    try:
                        # TODO:自動答題(新商品)
                        self.page.click('#question-1', timeout=1000)
                        print(f'{self.index}：第一次觀看需答題')
                        self.error_queue.put([self.url, '第一次觀看需答題'])
                        need_break = True
                    except Exception:
                        try:
                            self.page.wait_for_selector(
                                ".dialogify__body:has-text('廣告能量補充中')",
                                timeout=1000
                            )
                            print(f'{self.index}：廣告能量補充中')
                            self.error_queue.put([self.url, '廣告能量補充中'])
                            need_break = True
                        except Exception:
                            print(f'{self.index}：找不到"觀看廣告>確認"按鈕')
                            self.error_queue.put([self.url, '找不到"觀看廣告>確認"按鈕'])
                            print(self.page.url)
                            # time.sleep(10)
                            need_break = True
        return need_break

    def switch_to_ad_iframe(self, timeout=10):
        """切換到廣告視窗 iframe 內"""
        need_break = False
        try:
            frame_element_handle = self.page.wait_for_selector(
                'ins > div > iframe',
                timeout=timeout*1000
            )
            self.frame = frame_element_handle.content_frame()
        except Exception:
            print(f'{self.index}：找不到"廣告"視窗')
            self.error_queue.put([self.url, '找不到"廣告"視窗'])
            need_break = True
        return need_break

    def click_continue_watch_ad(self, timeout=20):
        """點擊'繼續有聲播放'按鈕，沒有則不需處理"""
        try:
            self.frame.click(
                '.videoAdUi .rewardDialogueWrapper:last-of-type .rewardResumebutton',
                timeout=timeout*1000
            )
        except Exception:
            pass

    def close_ad_iframe(self, timeout=10):
        """關閉影片，影片播放完畢或可跳過"""
        need_break = False
        try:
            self.frame.click(
                '#google-rewarded-video > img:nth-child(4), ' +
                '#close_button #close_button_icon, ' +
                '.videoAdUiSkipButtonExperimentalText',
                timeout=timeout*1000)
            #google-rewarded-video img[src="https://googleads.g.doubleclick.net/pagead/images/gmob/close-circle-30x30.png"]
        except Exception:
            # 判斷是否已經自動跳到抽獎
            print(self.page.url)
            # self.page.screenshot(path="example.png")
            if 'buyD' in self.page.url:
                return need_break
            try:
                # 出現"發生錯誤，請重新嘗試(1)"視窗
                self.frame.click(".dialogify__body:has-text('發生錯誤')", timeout=1000)
                print(f'{self.index}：發生錯誤，請重新嘗試')
                self.error_queue.put([self.url, '發生錯誤，請重新嘗試'])
                # driver.quit()
                need_break = True
            except Exception:
                print(f'{self.index}：廣告播放失敗 或 找不到關閉影片按鈕')
                self.error_queue.put([self.url, '廣告播放失敗 或 找不到關閉影片按鈕'])
                time.sleep(600)
                need_break = True
        return need_break

    def send_lottery_info(self, timeout=10):
        """送出抽獎資料 (已看完廣告)"""
        need_break = False
        try:
            # "我已閱讀注意事項，並確認兌換此商品"選擇 checkbox
            self.page.click('.agree-confirm-box', timeout=timeout*1000)
            # "確認兌換"按鈕
            self.page.click('.pbox-btn a.c-primary', timeout=3000)
        except Exception:
            print(f'{self.index}：找不到"我已閱讀注意事項，並確認兌換此商品"或"確認兌換"按鈕')
            self.error_queue.put([self.url, '找不到"我已閱讀注意事項，並確認兌換此商品"或"確認兌換"按鈕'])
            time.sleep(600)
            need_break = True
        return need_break

    def click_continue_exchange_goods(self, timeout=10):
        """點擊'確認兌換商品'彈跳視窗"""
        need_break = False
        try:
            self.page.click('.dialogify__content [type="submit"]', timeout=timeout*1000)
        except Exception:
            print(f'{self.index}：找不到"彈跳視窗內確定"按鈕')
            self.error_queue.put([self.url, '找不到"彈跳視窗內確定"按鈕'])
            # time.sleep(10)
            need_break = True
        return need_break


def exchange_all_goods(is_crawl=True, goods_urls=None):
    """兌換全部商品(觀看廣告)，使用多執行緒"""
    if is_crawl:
        items_url = get_goods_url()
        print('=' * 30)
        for goods in items_url:
            print('●', goods['name'], goods['price'])
            print('--->', goods['url'])
        print('=' * 30)
        user_input_text = input(f'(數量:{len(items_url)}) 確認是否抓取？(Y/n) ')
        if user_input_text == 'n' or user_input_text == 'N':
            return None
        urls = [items['url'] for items in items_url]
    else:
        urls = goods_urls

    if not urls:
        print('沒有商品網址')

    time_start = time.time()
    # 建立商品網址的 Queue
    url_queue = queue.Queue()
    # 建立失敗商品網址的 Queue
    error_queue = queue.Queue()

    # 將資料放入 Queue
    for url in urls:
        url_queue.put(url)

    # 設定執行緒數量
    # 同時執行太多，容易變慢或出現"廣告能量補充中"
    thread_num = int(config['settings']['thread_num'])
    if len(urls) < thread_num:
        thread_num = len(urls)
    threads = []
    for index in range(thread_num):
        threads.append(exchangeGoodsThread(index, url_queue, error_queue))
        threads[index].start()
        print(f'執行緒 {index} 開始運行')
        time.sleep(3)

    # 等待所有子執行緒結束
    for index in range(len(threads)):
        threads[index].join()

    print('========== 全數結束 ==========')
    print(f'耗時：{time.time()-time_start:.0f} 秒')
    # 列出發生錯誤的網址
    if error_queue.qsize() > 0:
        print('========== 異常任務 ==========')
        while error_queue.qsize() > 0:
            print(error_queue.get())


if __name__ == "__main__":
    # goods_urls = [
    #     'https://fuli.gamer.com.tw/shop_detail.php?sn=2423',
    #     'https://fuli.gamer.com.tw/shop_detail.php?sn=2386',
    # ]
    # exchange_all_goods(is_crawl=False, goods_urls=goods_urls)
    # exchange_all_goods(is_crawl=True)

    # 登入巴哈網站，儲存 cookie
    save_cookie()
