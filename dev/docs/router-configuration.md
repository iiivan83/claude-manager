# Конфигурация роутера MikroTik

Укрупнённая карта настроек домашнего роутера. За деталями — подключиться к роутеру.

## Подключение

- **Адрес:** 192.168.0.1
- **Протокол:** SSH, логин `ivan`
- **Пароль:** Parolvata22!

```bash
ssh ivan@192.168.0.1
```

## Роутер

- **Модель:** MikroTik L009UiGS, RouterOS 7.22.1
- **WAN:** PPPoE через ether1, динамический IP
- **LAN:** bridge (ether2–ether8), подсеть 192.168.0.0/24
- **Wi-Fi:** TP-Link Deco M5 ×4 (mesh), подключены к bridge
- **VPN:** WireGuard :51820, подсеть 10.200.200.0/24, peer — macbook-ivan

## Firewall — общая логика

- **Input:** accept WireGuard (UDP/51820), accept Winbox (TCP/8291 от address-list `admin`), стандартный defconf (established/related, drop invalid, ICMP, drop не из LAN)
- **Forward:** defconf + FastTrack + контентная фильтрация (см. ниже)
- **NAT:** masquerade LAN→WAN + DNS-перехват для Samsung TV

## Samsung TV (192.168.0.40) — полная блокировка

**Весь трафик заблокирован** — drop в forward chain перед FastTrack (добавлено 03.05.2026).

Дополнительно остались точечные блокировки `rutube.ru` и `vkvideo.ru` (DNS-подмена + TLS-SNI) — сработают при снятии полного блока.

### Снятие полной блокировки

```bash
# Найти и удалить правило
/ip firewall filter print where comment="Block ALL traffic for Samsung TV"
/ip firewall filter remove <номер>
```
