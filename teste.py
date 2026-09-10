import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

# Inicializa o navegador Google Chrome
driver = webdriver.Chrome()

try:
    # Acessa a página do Google
    driver.get("https://www.google.com")

    # Localiza a barra de pesquisa pelo nome do elemento
    search_box = driver.find_element(By.NAME, "q")

    # Digita "capivara" e pressiona a tecla ENTER
    search_box.send_keys("capivara" + Keys.ENTER)

    # Aguarda 5 segundos para visualização dos resultados
    time.sleep(5)

finally:
    # Encerra a sessão e fecha o navegador
    driver.quit()