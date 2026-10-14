#include "modbus_tcp.h"
#include "lwip/tcp.h"
#include "lwip/debug.h"
#include "lwip/def.h"  // 添加LwIP字节序转换函数
#include <string.h>

static err_t modbus_tcp_accept(void *arg, struct tcp_pcb *newpcb, err_t err);
static err_t modbus_tcp_recv(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err);
static void modbus_tcp_error(void *arg, err_t err);
static void modbus_tcp_close_client(Modbus_TCP_Server *server, Modbus_Client_Context *client);
static uint16_t modbus_get_staged_register(const Modbus_TCP_Server *server, uint16_t start_addr,
                                           uint16_t quantity, const uint16_t *staged_values, uint16_t address);

// 自定义字节序转换函数已被LwIP内置函数替换 (lwip_htons/lwip_ntohs)
// 使用LwIP内置函数具有更好的性能和优化

static uint8_t s_pending_software_reset = 0;

Modbus_Status Modbus_TCP_Init(Modbus_TCP_Server *server, Resistor_Handle *resistor_handle)
{
    if (server == NULL || resistor_handle == NULL) {
        return MODBUS_ERROR;
    }
    
    memset(server, 0, sizeof(Modbus_TCP_Server));
    server->resistor_handle = resistor_handle;
    server->server_running = 0;
    
    server->registers[MODBUS_REG_DEVICE_ID] = 0x5254; // "RT" for Resistor TCP
    server->registers[MODBUS_REG_FIRMWARE_VERSION] = 0x0102; // Version 1.2 (增加网络配置功能)
    server->registers[MODBUS_REG_SYSTEM_STATUS] = 0x0001; // System ready
    server->registers[MODBUS_REG_CHANNEL_ENABLE] = 0x0001; // CH1 supported/enabled for compatibility
    
    // 初始化网络控制寄存器
    server->registers[MODBUS_REG_NETWORK_CONTROL] = 0;
    server->registers[MODBUS_REG_SOFTWARE_RESET] = 0;  // 初始化软件重启寄存器
    
    for (int i = 0; i < MODBUS_MAX_CLIENTS; i++) {
        server->clients[i].active = 0;
        server->clients[i].pcb = NULL;
        server->clients[i].rx_index = 0;
        server->clients[i].server = server;
    }
    
    // 首次更新所有寄存器，包括从Flash加载网络配置
    Modbus_Update_Registers(server);
    
    // 强制从Flash加载网络配置到寄存器 (初始化时)
    Modbus_Update_Network_Registers(server, 1);

    return MODBUS_OK;
}

Modbus_Status Modbus_TCP_Start(Modbus_TCP_Server *server)
{
    if (server == NULL || server->server_running) {
        return MODBUS_ERROR;
    }
    
    server->listen_pcb = tcp_new();
    if (server->listen_pcb == NULL) {
        return MODBUS_ERROR;
    }
    
    err_t err = tcp_bind(server->listen_pcb, IP_ADDR_ANY, MODBUS_TCP_PORT);
    if (err != ERR_OK) {
        tcp_close(server->listen_pcb);
        server->listen_pcb = NULL;
        return MODBUS_ERROR;
    }
    
    server->listen_pcb = tcp_listen(server->listen_pcb);
    if (server->listen_pcb == NULL) {
        return MODBUS_ERROR;
    }
    
    tcp_arg(server->listen_pcb, server);
    tcp_accept(server->listen_pcb, modbus_tcp_accept);
    
    server->server_running = 1;
    
    return MODBUS_OK;
}

Modbus_Status Modbus_TCP_Stop(Modbus_TCP_Server *server)
{
    if (server == NULL || !server->server_running) {
        return MODBUS_ERROR;
    }
    
    for (int i = 0; i < MODBUS_MAX_CLIENTS; i++) {
        if (server->clients[i].active) {
            modbus_tcp_close_client(server, &server->clients[i]);
        }
    }
    
    if (server->listen_pcb != NULL) {
        tcp_close(server->listen_pcb);
        server->listen_pcb = NULL;
    }
    
    server->server_running = 0;
    
    return MODBUS_OK;
}

void Modbus_TCP_Process(Modbus_TCP_Server *server)
{
    if (server == NULL || !server->server_running) {
        return;
    }

    Modbus_Update_Registers(server);

    if (s_pending_software_reset) {
        s_pending_software_reset = 0;
        NetworkConfig_SoftwareReset();
    }
}

static err_t modbus_tcp_accept(void *arg, struct tcp_pcb *newpcb, err_t err)
{
    Modbus_TCP_Server *server = (Modbus_TCP_Server *)arg;
    
    if (err != ERR_OK || newpcb == NULL) {
        return ERR_VAL;
    }
    
    // 只允许一个连接，新连接踢掉旧连接
    Modbus_Client_Context *client = &server->clients[0];
    if (client->active) {
        modbus_tcp_close_client(server, client);
    }
    
    client->pcb = newpcb;
    client->active = 1;
    client->rx_index = 0;

    // 禁用Nagle算法以减少小包延迟 (优化Modbus TCP响应速度)
    tcp_nagle_disable(newpcb);

    tcp_arg(newpcb, client);
    tcp_recv(newpcb, modbus_tcp_recv);
    tcp_err(newpcb, modbus_tcp_error);

    tcp_accepted(server->listen_pcb);
    
    return ERR_OK;
}

static err_t modbus_tcp_recv(void *arg, struct tcp_pcb *tpcb, struct pbuf *p, err_t err)
{
    Modbus_Client_Context *client = (Modbus_Client_Context *)arg;
    
    // If the client has gracefully closed the connection, p will be NULL.
    if (p == NULL) {
        if (client != NULL) {
            // This call cleans up the client resources and closes the PCB
            modbus_tcp_close_client(client->server, client);
        }
        return ERR_OK;
    }

    // Handle other non-fatal errors. Returning an error will cause LwIP to
    // abort the connection and call the error callback.
    if (err != ERR_OK) {
        if (p != NULL) {
            pbuf_free(p);
        }
        return err;
    }
    
    if (client == NULL || !client->active) {
        pbuf_free(p);
        return ERR_OK;
    }
    
    Modbus_TCP_Server *server = client->server;
    if (server == NULL) {
        pbuf_free(p);
        return ERR_OK;
    }
    
    struct pbuf *q = p;
    while (q != NULL) {
        if (client->rx_index + q->len > MODBUS_TCP_MAX_FRAME_LENGTH) {
            pbuf_free(p);
            modbus_tcp_close_client(server, client);
            return ERR_OK;
        }
        
        memcpy(&client->rx_buffer[client->rx_index], q->payload, q->len);
        client->rx_index += q->len;
        q = q->next;
    }
    
    tcp_recved(tpcb, p->tot_len);
    pbuf_free(p);
    
    while (client->rx_index >= MODBUS_MBAP_PREFIX_LENGTH) {
        Modbus_TCP_Header *header = (Modbus_TCP_Header *)client->rx_buffer;
        uint16_t expected_length = lwip_ntohs(header->length) + MODBUS_MBAP_PREFIX_LENGTH;

        if (expected_length < (MODBUS_MBAP_HEADER_LENGTH + 1) ||
            expected_length > MODBUS_TCP_MAX_FRAME_LENGTH) {
            modbus_tcp_close_client(server, client);
            return ERR_OK;
        }

        if (client->rx_index < expected_length) {
            break;
        }

        uint16_t response_len = 0;
        Modbus_Status status = Modbus_Process_Request(server, client,
                                                      client->rx_buffer, expected_length,
                                                      client->tx_buffer, &response_len);

        if (status == MODBUS_OK && response_len > 0) {
            err_t write_err = tcp_write(tpcb, client->tx_buffer, response_len, TCP_WRITE_FLAG_COPY);
            if (write_err != ERR_OK) {
                modbus_tcp_close_client(server, client);
                return ERR_OK;
            }

            err_t output_err = tcp_output(tpcb);
            if (output_err != ERR_OK) {
                modbus_tcp_close_client(server, client);
                return ERR_OK;
            }
        }

        uint16_t remaining = client->rx_index - expected_length;
        if (remaining > 0) {
            memmove(client->rx_buffer, &client->rx_buffer[expected_length], remaining);
        }
        client->rx_index = remaining;
    }
    
    return ERR_OK;
}

static void modbus_tcp_error(void *arg, err_t err)
{
    Modbus_Client_Context *client = (Modbus_Client_Context *)arg;
    
    if (client != NULL) {
        client->active = 0;
        client->pcb = NULL;
        client->rx_index = 0;
    }
}

static void modbus_tcp_close_client(Modbus_TCP_Server *server, Modbus_Client_Context *client)
{
    if (client == NULL || !client->active) {
        return;
    }
    
    if (client->pcb != NULL) {
        tcp_arg(client->pcb, NULL);
        tcp_recv(client->pcb, NULL);
        tcp_err(client->pcb, NULL);
        tcp_close(client->pcb);
        client->pcb = NULL;
    }
    
    client->active = 0;
    client->rx_index = 0;
}

static uint16_t modbus_get_staged_register(const Modbus_TCP_Server *server, uint16_t start_addr,
                                           uint16_t quantity, const uint16_t *staged_values, uint16_t address)
{
    uint16_t end_addr = start_addr + quantity;
    if (address >= start_addr && address < end_addr) {
        return staged_values[address - start_addr];
    }
    return server->registers[address];
}

Modbus_Status Modbus_Process_Request(Modbus_TCP_Server *server, Modbus_Client_Context *client,
                                     uint8_t *request, uint16_t request_len,
                                     uint8_t *response, uint16_t *response_len)
{
    if (server == NULL || client == NULL || request == NULL || response == NULL || response_len == NULL) {
        return MODBUS_ERROR;
    }
    
    if (request_len < MODBUS_MBAP_HEADER_LENGTH) {
        return MODBUS_ERROR;
    }
    
    Modbus_TCP_Header *req_header = (Modbus_TCP_Header *)request;
    uint8_t *pdu = request + MODBUS_MBAP_HEADER_LENGTH;
    uint16_t pdu_len = request_len - MODBUS_MBAP_HEADER_LENGTH;
    uint8_t function_code = pdu[0];
    
    Modbus_TCP_Header *resp_header = (Modbus_TCP_Header *)response;
    uint8_t *resp_pdu = response + MODBUS_MBAP_HEADER_LENGTH;
    
    resp_header->transaction_id = req_header->transaction_id;
    resp_header->protocol_id = req_header->protocol_id;
    resp_header->unit_id = req_header->unit_id;
    
    *response_len = MODBUS_MBAP_HEADER_LENGTH;
    
    switch (function_code) {
        case MODBUS_FUNCTION_READ_HOLDING_REGS:
        case MODBUS_FUNCTION_READ_INPUT_REGS: {
            if (pdu_len < 5) { // func(1) + start_addr(2) + quantity(2)
                goto exception_illegal_data_value;
            }
            
            Modbus_Read_Request *read_req = (Modbus_Read_Request *)pdu;
            uint16_t start_addr = lwip_ntohs(read_req->start_address);
            uint16_t quantity = lwip_ntohs(read_req->quantity);
            
            if (quantity == 0 || quantity > 125 || start_addr + quantity > MODBUS_TOTAL_REGISTERS) {
                goto exception_illegal_data_addr;
            }
            
            Modbus_Read_Response *read_resp = (Modbus_Read_Response *)resp_pdu;
            read_resp->function_code = function_code;
            read_resp->byte_count = quantity * 2;
            
            for (uint16_t i = 0; i < quantity; i++) {
                read_resp->values[i] = lwip_htons(server->registers[start_addr + i]);
            }
            
            uint16_t resp_pdu_len = 2 + read_resp->byte_count; // func(1) + byte_count(1) + values
            resp_header->length = lwip_htons(1 + resp_pdu_len); // unit_id(1) + PDU
            *response_len += resp_pdu_len;
            break;
        }
        
        case MODBUS_FUNCTION_WRITE_SINGLE_REG: {
            if (pdu_len < 5) { // func(1) + address(2) + value(2)
                goto exception_illegal_data_value;
            }
            
            Modbus_Write_Single_Request *write_req = (Modbus_Write_Single_Request *)pdu;
            uint16_t address = lwip_ntohs(write_req->start_address);
            uint16_t value = lwip_ntohs(write_req->value);
            
            if (address >= MODBUS_TOTAL_REGISTERS) {
                goto exception_illegal_data_addr;
            }
            
            if (address >= MODBUS_REG_CH1_RESISTANCE_H && address <= MODBUS_REG_CH8_RESISTANCE_L) {
                uint8_t channel = (address - MODBUS_REG_CH1_RESISTANCE_H) / 2;
                uint8_t is_high = (address - MODBUS_REG_CH1_RESISTANCE_H) % 2 == 0;
                
                uint16_t high_word, low_word;
                if (is_high) {
                    high_word = value;
                    low_word = server->registers[address + 1];
                } else {
                    high_word = server->registers[address - 1];
                    low_word = value;
                }
                
                uint32_t new_resistance = ((uint32_t)high_word << 16) | low_word;
                
                if (new_resistance < Resistor_GetMinResistance() || new_resistance > Resistor_GetMaxResistance()) {
                    goto exception_illegal_data_value;
                }
                if (new_resistance % Resistor_GetResolution() != 0) {
                    goto exception_illegal_data_value;
                }

                Resistor_Status status = Resistor_SetChannelValue(server->resistor_handle, channel, new_resistance);
                if (status != RESISTOR_OK) {
                    goto exception_slave_device_fail;
                }
                
                server->registers[address] = value;

            } else if (address == MODBUS_REG_CHANNEL_ENABLE) {
                if ((value & 0x0001U) == 0U) {
                    if (Resistor_DisableChannel(server->resistor_handle, RESISTOR_CH1) != RESISTOR_OK) {
                        goto exception_slave_device_fail;
                    }
                }
                server->registers[address] = (uint16_t)(value & 0x0001U);
            } else if (address >= MODBUS_REG_IP_ADDR_HIGH && address <= MODBUS_REG_GATEWAY_LOW) {
                // 网络配置寄存器 - 只缓存，不立即应用
                server->registers[address] = value;
            } else if (address == MODBUS_REG_NETWORK_CONTROL) {
                // 处理网络控制命令
                if (value & NETWORK_CTRL_APPLY_CONFIG) {
                    // 应用网络配置
                    NetworkConfig_t new_config;
                    new_config.magic = NETWORK_CONFIG_MAGIC;
                    
                    // 从寄存器中提取IP地址
                    uint16_t ip_high = server->registers[MODBUS_REG_IP_ADDR_HIGH];
                    uint16_t ip_low = server->registers[MODBUS_REG_IP_ADDR_LOW];
                    new_config.ip_address[0] = (ip_high >> 8) & 0xFF;
                    new_config.ip_address[1] = ip_high & 0xFF;
                    new_config.ip_address[2] = (ip_low >> 8) & 0xFF;
                    new_config.ip_address[3] = ip_low & 0xFF;
                    
                    // 从寄存器中提取子网掩码
                    uint16_t nm_high = server->registers[MODBUS_REG_NETMASK_HIGH];
                    uint16_t nm_low = server->registers[MODBUS_REG_NETMASK_LOW];
                    new_config.netmask[0] = (nm_high >> 8) & 0xFF;
                    new_config.netmask[1] = nm_high & 0xFF;
                    new_config.netmask[2] = (nm_low >> 8) & 0xFF;
                    new_config.netmask[3] = nm_low & 0xFF;
                    
                    // 从寄存器中提取网关地址
                    uint16_t gw_high = server->registers[MODBUS_REG_GATEWAY_HIGH];
                    uint16_t gw_low = server->registers[MODBUS_REG_GATEWAY_LOW];
                    new_config.gateway[0] = (gw_high >> 8) & 0xFF;
                    new_config.gateway[1] = gw_high & 0xFF;
                    new_config.gateway[2] = (gw_low >> 8) & 0xFF;
                    new_config.gateway[3] = gw_low & 0xFF;
                    
                    new_config.dhcp_enable = 0; // 固定IP
                    memset(new_config.reserved, 0, sizeof(new_config.reserved));
                    new_config.checksum = NetworkConfig_CalculateChecksum(&new_config);
                    
                    // 保存配置
                    NetworkConfig_Status status = NetworkConfig_Save(&new_config);
                    if (status == NETWORK_CONFIG_OK) {
                        server->registers[MODBUS_REG_NETWORK_CONTROL] |= NETWORK_CTRL_REBOOT_REQUIRED;
                        // 立即更新寄存器显示新配置
                        Modbus_Update_Network_Registers(server, 1);
                    }
                }
                
                if (value & NETWORK_CTRL_RESTORE_DEFAULTS) {
                    // 恢复默认配置
                    NetworkConfig_Status status = NetworkConfig_RestoreDefaults();
                    if (status == NETWORK_CONFIG_OK) {
                        server->registers[MODBUS_REG_NETWORK_CONTROL] |= NETWORK_CTRL_REBOOT_REQUIRED;
                        // 更新寄存器显示默认值
                        Modbus_Update_Network_Registers(server, 1);
                    }
                }
                
                if (value & NETWORK_CTRL_SOFTWARE_RESET) {
                    // 软件重启 - 延迟到响应发送后执行
                    s_pending_software_reset = 1;
                }
                
                // 清除触发控制位，但保留状态位 (如REBOOT_REQUIRED)
                server->registers[address] = server->registers[address] & ~(NETWORK_CTRL_APPLY_CONFIG | NETWORK_CTRL_RESTORE_DEFAULTS | NETWORK_CTRL_SOFTWARE_RESET);
            } else if (address == MODBUS_REG_SOFTWARE_RESET) {
                // 处理软件重启寄存器 (0x011F)
                if (value == 1) {
                    // 延迟到Modbus响应发送后再执行重启
                    s_pending_software_reset = 1;
                }
                // 不保存该寄存器的值，因为它是触发型寄存器
            } else {
                server->registers[address] = value;
            }
            
            Modbus_Write_Single_Response *write_resp = (Modbus_Write_Single_Response *)resp_pdu;
            write_resp->function_code = function_code;
            write_resp->address = write_req->start_address;
            write_resp->value = write_req->value;
            
            uint16_t resp_pdu_len = 5; // func(1) + address(2) + value(2)
            resp_header->length = lwip_htons(1 + resp_pdu_len);
            *response_len += resp_pdu_len;
            break;
        }
        
        case MODBUS_FUNCTION_WRITE_MULTIPLE_REGS: {
            if (pdu_len < 7) { // func(1) + start_addr(2) + quantity(2) + byte_count(1) + ...
                goto exception_illegal_data_value;
            }
            
            Modbus_Write_Multiple_Request *write_req = (Modbus_Write_Multiple_Request *)pdu;
            uint16_t start_addr = lwip_ntohs(write_req->start_address);
            uint16_t quantity = lwip_ntohs(write_req->quantity);
            uint8_t byte_count = write_req->byte_count;
            
            if (quantity == 0 || quantity > 123 || byte_count != quantity * 2 ||
                start_addr + quantity > MODBUS_TOTAL_REGISTERS || pdu_len < 6 + byte_count) {
                goto exception_illegal_data_addr;
            }
            
            uint16_t end_addr = start_addr + quantity;
            uint16_t staged_values[123];

            // Stage new register values first; commit only after validation/hardware update succeeds.
            for (uint16_t i = 0; i < quantity; i++) {
                staged_values[i] = lwip_ntohs(write_req->values[i]);
            }

            // Apply resistor updates for channels touched by this request.
            for (uint8_t channel = 0; channel < RESISTOR_CHANNELS_TOTAL; ++channel) {
                uint16_t high_addr = MODBUS_REG_CH1_RESISTANCE_H + (uint16_t)channel * 2U;
                uint16_t low_addr = high_addr + 1U;
                uint8_t high_touched = (high_addr >= start_addr && high_addr < end_addr);
                uint8_t low_touched = (low_addr >= start_addr && low_addr < end_addr);

                if (!high_touched && !low_touched) {
                    continue;
                }

                uint16_t high_word = modbus_get_staged_register(server, start_addr, quantity, staged_values, high_addr);
                uint16_t low_word = modbus_get_staged_register(server, start_addr, quantity, staged_values, low_addr);
                uint32_t new_resistance = ((uint32_t)high_word << 16) | low_word;

                if (new_resistance < Resistor_GetMinResistance() || new_resistance > Resistor_GetMaxResistance()) {
                    goto exception_illegal_data_value;
                }
                if (new_resistance % Resistor_GetResolution() != 0) {
                    goto exception_illegal_data_value;
                }

                Resistor_Status status = Resistor_SetChannelValue(server->resistor_handle, channel, new_resistance);
                if (status != RESISTOR_OK) {
                    goto exception_slave_device_fail;
                }
            }

            for (uint16_t i = 0; i < quantity; i++) {
                server->registers[start_addr + i] = staged_values[i];
            }
            
            // 处理网络控制寄存器（如果在写入范围内）
            if (start_addr <= MODBUS_REG_NETWORK_CONTROL && end_addr > MODBUS_REG_NETWORK_CONTROL) {
                uint16_t ctrl_value = server->registers[MODBUS_REG_NETWORK_CONTROL];
                
                if (ctrl_value & NETWORK_CTRL_APPLY_CONFIG) {
                    NetworkConfig_t new_config;
                    new_config.magic = NETWORK_CONFIG_MAGIC;
                    uint16_t ip_high = server->registers[MODBUS_REG_IP_ADDR_HIGH];
                    uint16_t ip_low = server->registers[MODBUS_REG_IP_ADDR_LOW];
                    new_config.ip_address[0] = (ip_high >> 8) & 0xFF;
                    new_config.ip_address[1] = ip_high & 0xFF;
                    new_config.ip_address[2] = (ip_low >> 8) & 0xFF;
                    new_config.ip_address[3] = ip_low & 0xFF;
                    uint16_t nm_high = server->registers[MODBUS_REG_NETMASK_HIGH];
                    uint16_t nm_low = server->registers[MODBUS_REG_NETMASK_LOW];
                    new_config.netmask[0] = (nm_high >> 8) & 0xFF;
                    new_config.netmask[1] = nm_high & 0xFF;
                    new_config.netmask[2] = (nm_low >> 8) & 0xFF;
                    new_config.netmask[3] = nm_low & 0xFF;
                    uint16_t gw_high = server->registers[MODBUS_REG_GATEWAY_HIGH];
                    uint16_t gw_low = server->registers[MODBUS_REG_GATEWAY_LOW];
                    new_config.gateway[0] = (gw_high >> 8) & 0xFF;
                    new_config.gateway[1] = gw_high & 0xFF;
                    new_config.gateway[2] = (gw_low >> 8) & 0xFF;
                    new_config.gateway[3] = gw_low & 0xFF;
                    new_config.dhcp_enable = 0;
                    memset(new_config.reserved, 0, sizeof(new_config.reserved));
                    new_config.checksum = NetworkConfig_CalculateChecksum(&new_config);
                    if (NetworkConfig_Save(&new_config) == NETWORK_CONFIG_OK) {
                        server->registers[MODBUS_REG_NETWORK_CONTROL] |= NETWORK_CTRL_REBOOT_REQUIRED;
                        Modbus_Update_Network_Registers(server, 1);
                    }
                }
                if (ctrl_value & NETWORK_CTRL_RESTORE_DEFAULTS) {
                    if (NetworkConfig_RestoreDefaults() == NETWORK_CONFIG_OK) {
                        server->registers[MODBUS_REG_NETWORK_CONTROL] |= NETWORK_CTRL_REBOOT_REQUIRED;
                        Modbus_Update_Network_Registers(server, 1);
                    }
                }
                if (ctrl_value & NETWORK_CTRL_SOFTWARE_RESET) {
                    s_pending_software_reset = 1;
                }
                server->registers[MODBUS_REG_NETWORK_CONTROL] &= ~(NETWORK_CTRL_APPLY_CONFIG | NETWORK_CTRL_RESTORE_DEFAULTS | NETWORK_CTRL_SOFTWARE_RESET);
            }
            
            // 处理软件重启寄存器（如果在写入范围内）
            if (start_addr <= MODBUS_REG_SOFTWARE_RESET && end_addr > MODBUS_REG_SOFTWARE_RESET) {
                if (server->registers[MODBUS_REG_SOFTWARE_RESET] == 1) {
                    s_pending_software_reset = 1;
                }
            }
            
            Modbus_Write_Multiple_Response *write_resp = (Modbus_Write_Multiple_Response *)resp_pdu;
            write_resp->function_code = function_code;
            write_resp->start_address = write_req->start_address;
            write_resp->quantity = write_req->quantity;
            
            uint16_t resp_pdu_len = 5; // func(1) + start_addr(2) + quantity(2)
            resp_header->length = lwip_htons(1 + resp_pdu_len);
            *response_len += resp_pdu_len;
            break;
        }
        
        default:
            goto exception_illegal_function;
    }
    
    return MODBUS_OK;
    
exception_illegal_function:
    {
        Modbus_Exception_Response *exc_resp = (Modbus_Exception_Response *)resp_pdu;
        exc_resp->function_code = function_code | 0x80;
        exc_resp->exception_code = MODBUS_EXCEPTION_ILLEGAL_FUNCTION;
        uint16_t resp_pdu_len = 2; // func(1) + exception_code(1)
        resp_header->length = lwip_htons(1 + resp_pdu_len);
        *response_len += resp_pdu_len;
    }
    return MODBUS_OK;
    
exception_illegal_data_addr:
    {
        Modbus_Exception_Response *exc_resp = (Modbus_Exception_Response *)resp_pdu;
        exc_resp->function_code = function_code | 0x80;
        exc_resp->exception_code = MODBUS_EXCEPTION_ILLEGAL_DATA_ADDR;
        uint16_t resp_pdu_len = 2;
        resp_header->length = lwip_htons(1 + resp_pdu_len);
        *response_len += resp_pdu_len;
    }
    return MODBUS_OK;
    
exception_illegal_data_value:
    {
        Modbus_Exception_Response *exc_resp = (Modbus_Exception_Response *)resp_pdu;
        exc_resp->function_code = function_code | 0x80;
        exc_resp->exception_code = MODBUS_EXCEPTION_ILLEGAL_DATA_VALUE;
        uint16_t resp_pdu_len = 2;
        resp_header->length = lwip_htons(1 + resp_pdu_len);
        *response_len += resp_pdu_len;
    }
    return MODBUS_OK;
    
exception_slave_device_fail:
    {
        Modbus_Exception_Response *exc_resp = (Modbus_Exception_Response *)resp_pdu;
        exc_resp->function_code = function_code | 0x80;
        exc_resp->exception_code = MODBUS_EXCEPTION_SLAVE_DEVICE_FAIL;
        uint16_t resp_pdu_len = 2;
        resp_header->length = lwip_htons(1 + resp_pdu_len);
        *response_len += resp_pdu_len;
    }
    return MODBUS_OK;
}

void Modbus_Update_Registers(Modbus_TCP_Server *server)
{
    if (server == NULL || server->resistor_handle == NULL) {
        return;
    }
    
    // 更新电阻值寄存器
    for (int i = 0; i < RESISTOR_CHANNELS_TOTAL; i++) {
        uint32_t resistance;
        if (Resistor_GetChannelValue(server->resistor_handle, i, &resistance) == RESISTOR_OK) {
            server->registers[MODBUS_REG_CH1_RESISTANCE_H + i * 2] = (resistance >> 16) & 0xFFFF;
            server->registers[MODBUS_REG_CH1_RESISTANCE_L + i * 2] = resistance & 0xFFFF;
        }
    }

    // 系统状态寄存器
    server->registers[MODBUS_REG_SYSTEM_STATUS] = Resistor_IsInitialized(server->resistor_handle) ? 0x0001 : 0x0000;

    // 兼容状态寄存器: bit0 表示CH1当前是否有非0阻值输出
    server->registers[MODBUS_REG_RELAY_STATUS] = server->resistor_handle->channel_enabled[RESISTOR_CH1] ? 0x0001 : 0x0000;
    
    // 更新网络配置寄存器 (定期更新时不强制从Flash加载)
    Modbus_Update_Network_Registers(server, 0);
}

void Modbus_Update_Network_Registers(Modbus_TCP_Server *server, uint8_t force_from_flash)
{
    if (server == NULL) {
        return;
    }
    
    // 如果force_from_flash为真，则从Flash读取配置并更新寄存器
    // 否则只更新状态寄存器
    if (force_from_flash) {
        NetworkConfig_t current_config;
        NetworkConfig_Status status = NetworkConfig_GetCurrentConfig(&current_config);

        if (status == NETWORK_CONFIG_OK) {
            // 更新IP地址寄存器
            server->registers[MODBUS_REG_IP_ADDR_HIGH] = (current_config.ip_address[0] << 8) | current_config.ip_address[1];
            server->registers[MODBUS_REG_IP_ADDR_LOW] = (current_config.ip_address[2] << 8) | current_config.ip_address[3];
            
            // 更新子网掩码寄存器
            server->registers[MODBUS_REG_NETMASK_HIGH] = (current_config.netmask[0] << 8) | current_config.netmask[1];
            server->registers[MODBUS_REG_NETMASK_LOW] = (current_config.netmask[2] << 8) | current_config.netmask[3];
            
            // 更新网关地址寄存器
            server->registers[MODBUS_REG_GATEWAY_HIGH] = (current_config.gateway[0] << 8) | current_config.gateway[1];
            server->registers[MODBUS_REG_GATEWAY_LOW] = (current_config.gateway[2] << 8) | current_config.gateway[3];
            
        }
    }
}
