#include "io/io.hpp"
#include "core/geometry.hpp"
#include "miniz.h"
#include "nlohmann/json.hpp"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <iomanip>
#include <map>
#include <sstream>
#include <stdexcept>

namespace tin {

using json = nlohmann::json;

#ifdef _WIN32
std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) return {};
    int len = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (len <= 0) return {};
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), out.data(), len);
    return out;
}

std::string wide_to_utf8(const std::wstring& ws) {
    if (ws.empty()) return {};
    int len = WideCharToMultiByte(CP_UTF8, 0, ws.data(), static_cast<int>(ws.size()), nullptr, 0, nullptr, nullptr);
    if (len <= 0) return {};
    std::string out(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, ws.data(), static_cast<int>(ws.size()), out.data(), len, nullptr, nullptr);
    return out;
}
#else
std::wstring utf8_to_wide(const std::string& s) {
    std::wstring out;
    out.reserve(s.size());
    size_t i = 0;
    while (i < s.size()) {
        std::uint32_t cp = 0;
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80) {
            cp = c;
            ++i;
        } else if ((c & 0xE0) == 0xC0 && i + 1 < s.size()) {
            cp = ((c & 0x1F) << 6) | (static_cast<unsigned char>(s[i + 1]) & 0x3F);
            i += 2;
        } else if ((c & 0xF0) == 0xE0 && i + 2 < s.size()) {
            cp = ((c & 0x0F) << 12) |
                 ((static_cast<unsigned char>(s[i + 1]) & 0x3F) << 6) |
                 (static_cast<unsigned char>(s[i + 2]) & 0x3F);
            i += 3;
        } else if ((c & 0xF8) == 0xF0 && i + 3 < s.size()) {
            cp = ((c & 0x07) << 18) |
                 ((static_cast<unsigned char>(s[i + 1]) & 0x3F) << 12) |
                 ((static_cast<unsigned char>(s[i + 2]) & 0x3F) << 6) |
                 (static_cast<unsigned char>(s[i + 3]) & 0x3F);
            i += 4;
        } else {
            cp = c;
            ++i;
        }
        out.push_back(static_cast<wchar_t>(cp));
    }
    return out;
}

std::string wide_to_utf8(const std::wstring& ws) {
    std::string out;
    out.reserve(ws.size() * 2);
    for (wchar_t wc : ws) {
        std::uint32_t cp = static_cast<std::uint32_t>(wc);
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }
    return out;
}
#endif

namespace {

std::vector<std::uint8_t> read_file_bytes(const std::string& utf8_path) {
#ifdef _WIN32
    std::wstring wp = utf8_to_wide(utf8_path);
    FILE* fp = _wfopen(wp.c_str(), L"rb");
#else
    FILE* fp = std::fopen(utf8_path.c_str(), "rb");
#endif
    if (!fp) {
        throw std::runtime_error("无法打开文件: " + utf8_path);
    }
    std::fseek(fp, 0, SEEK_END);
    long sz = std::ftell(fp);
    std::fseek(fp, 0, SEEK_SET);
    std::vector<std::uint8_t> buf(sz > 0 ? static_cast<size_t>(sz) : 0);
    if (!buf.empty()) {
        size_t n = std::fread(buf.data(), 1, buf.size(), fp);
        buf.resize(n);
    }
    std::fclose(fp);
    return buf;
}

void write_file_bytes(const std::string& utf8_path, const void* data, size_t size) {
#ifdef _WIN32
    std::wstring wp = utf8_to_wide(utf8_path);
    FILE* fp = _wfopen(wp.c_str(), L"wb");
#else
    FILE* fp = std::fopen(utf8_path.c_str(), "wb");
#endif
    if (!fp) {
        throw std::runtime_error("无法写入文件: " + utf8_path);
    }
    if (size > 0 && std::fwrite(data, 1, size, fp) != size) {
        std::fclose(fp);
        throw std::runtime_error("写入文件失败: " + utf8_path);
    }
    std::fclose(fp);
}

void write_utf8_text(const std::string& utf8_path, const std::string& text, bool with_bom = false) {
    std::string content = with_bom ? ("\xEF\xBB\xBF" + text) : text;
    write_file_bytes(utf8_path, content.data(), content.size());
}

std::string decode_text_auto(const std::vector<std::uint8_t>& bytes) {
    if (bytes.empty()) return {};
    size_t offset = 0;
    if (bytes.size() >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF) {
        offset = 3;
    } else if (bytes.size() >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE) {
        size_t n_u16 = (bytes.size() - 2) / 2;
        std::wstring ws(n_u16, L'\0');
        for (size_t i = 0; i < n_u16; ++i) {
            std::uint16_t u16 = static_cast<std::uint16_t>(bytes[2 + i * 2]) |
                                (static_cast<std::uint16_t>(bytes[2 + i * 2 + 1]) << 8);
            ws[i] = static_cast<wchar_t>(u16);
        }
        return wide_to_utf8(ws);
    }

    const char* ptr = reinterpret_cast<const char*>(bytes.data() + offset);
    int len = static_cast<int>(bytes.size() - offset);
    if (len <= 0) return {};

#ifdef _WIN32
    // 优先严格校验 UTF-8
    int wlen = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, ptr, len, nullptr, 0);
    if (wlen > 0) {
        return std::string(ptr, len);
    }
    // 回退 GBK (936)
    wlen = MultiByteToWideChar(936, 0, ptr, len, nullptr, 0);
    if (wlen > 0) {
        std::wstring ws(wlen, L'\0');
        MultiByteToWideChar(936, 0, ptr, len, ws.data(), wlen);
        return wide_to_utf8(ws);
    }
#endif
    return std::string(ptr, len);
}

std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && (static_cast<unsigned char>(s[a]) <= ' ')) ++a;
    while (b > a && (static_cast<unsigned char>(s[b - 1]) <= ' ')) --b;
    return s.substr(a, b - a);
}

std::string to_lower_ascii(std::string s) {
    for (char& ch : s) {
        if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch - 'A' + 'a');
    }
    return s;
}

std::string file_extension_lower(const std::string& path) {
    size_t dot = path.find_last_of('.');
    size_t slash = path.find_last_of("/\\");
    if (dot == std::string::npos || (slash != std::string::npos && dot < slash)) return {};
    return to_lower_ascii(path.substr(dot));
}

bool parse_double_strict(const std::string& s, double& out) {
    std::string t = trim(s);
    if (t.empty()) return false;
    char* end = nullptr;
    out = std::strtod(t.c_str(), &end);
    if (end == t.c_str() || *end != '\0') return false;
    return std::isfinite(out);
}

std::vector<std::string> split_line_fields(const std::string& line) {
    std::vector<std::string> out;
    if (line.find(',') != std::string::npos) {
        std::stringstream ss(line);
        std::string item;
        while (std::getline(ss, item, ',')) {
            out.push_back(trim(item));
        }
        return out;
    }
    std::istringstream ss(line);
    std::string tok;
    while (ss >> tok) out.push_back(tok);
    return out;
}

std::string unescape_xml(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '&') {
            size_t semi = s.find(';', i);
            if (semi != std::string::npos && semi - i <= 10) {
                std::string ent = s.substr(i + 1, semi - i - 1);
                if (ent == "amp") { out.push_back('&'); i = semi; continue; }
                if (ent == "lt") { out.push_back('<'); i = semi; continue; }
                if (ent == "gt") { out.push_back('>'); i = semi; continue; }
                if (ent == "quot") { out.push_back('"'); i = semi; continue; }
                if (ent == "apos") { out.push_back('\''); i = semi; continue; }
                if (!ent.empty() && ent[0] == '#') {
                    int code = 0;
                    if (ent.size() > 1 && (ent[1] == 'x' || ent[1] == 'X')) {
                        code = static_cast<int>(std::strtol(ent.c_str() + 2, nullptr, 16));
                    } else {
                        code = std::atoi(ent.c_str() + 1);
                    }
                    if (code > 0 && code < 0x10000) {
                        wchar_t wc = static_cast<wchar_t>(code);
                        out += wide_to_utf8(std::wstring(1, wc));
                        i = semi;
                        continue;
                    }
                }
            }
        }
        out.push_back(s[i]);
    }
    return out;
}

std::string extract_attr(const std::string& tag_body, const std::string& attr) {
    std::string pat1 = attr + "=\"";
    size_t p = tag_body.find(pat1);
    char quote = '"';
    if (p == std::string::npos) {
        std::string pat2 = attr + "='";
        p = tag_body.find(pat2);
        quote = '\'';
        if (p == std::string::npos) return {};
        p += pat2.size();
    } else {
        p += pat1.size();
    }
    size_t e = tag_body.find(quote, p);
    if (e == std::string::npos) return {};
    return tag_body.substr(p, e - p);
}

int col_index_from_ref(const std::string& ref) {
    int col = 0;
    bool has = false;
    for (char ch : ref) {
        if (ch >= 'A' && ch <= 'Z') {
            col = col * 26 + (ch - 'A' + 1);
            has = true;
        } else if (ch >= 'a' && ch <= 'z') {
            col = col * 26 + (ch - 'a' + 1);
            has = true;
        } else {
            break;
        }
    }
    return has ? (col - 1) : -1;
}

std::string collect_t_tags(const std::string& xml_slice) {
    std::string result;
    size_t pos = 0;
    while (true) {
        size_t t0 = xml_slice.find("<t", pos);
        if (t0 == std::string::npos) break;
        char nxt = (t0 + 2 < xml_slice.size()) ? xml_slice[t0 + 2] : '\0';
        if (nxt != '>' && nxt != ' ' && nxt != '/') {
            pos = t0 + 2;
            continue;
        }
        size_t gt = xml_slice.find('>', t0);
        if (gt == std::string::npos) break;
        if (gt > t0 && xml_slice[gt - 1] == '/') {
            pos = gt + 1;
            continue;
        }
        size_t t1 = xml_slice.find("</t>", gt + 1);
        if (t1 == std::string::npos) break;
        result += unescape_xml(xml_slice.substr(gt + 1, t1 - gt - 1));
        pos = t1 + 4;
    }
    return result;
}

std::vector<std::string> parse_shared_strings(const std::string& xml) {
    std::vector<std::string> list;
    size_t pos = 0;
    while (true) {
        size_t s0 = xml.find("<si", pos);
        if (s0 == std::string::npos) break;
        size_t gt = xml.find('>', s0);
        if (gt == std::string::npos) break;
        if (xml[gt - 1] == '/') {
            list.emplace_back();
            pos = gt + 1;
            continue;
        }
        size_t s1 = xml.find("</si>", gt + 1);
        if (s1 == std::string::npos) break;
        list.push_back(collect_t_tags(xml.substr(gt + 1, s1 - gt - 1)));
        pos = s1 + 5;
    }
    return list;
}

std::vector<std::vector<std::string>> parse_sheet_rows(const std::string& xml, const std::vector<std::string>& sst) {
    std::vector<std::vector<std::string>> rows;
    size_t pos = 0;
    while (true) {
        size_t r0 = xml.find("<row", pos);
        if (r0 == std::string::npos) break;
        char nxt = (r0 + 4 < xml.size()) ? xml[r0 + 4] : '\0';
        if (nxt != '>' && nxt != ' ') { pos = r0 + 4; continue; }
        size_t rgt = xml.find('>', r0);
        if (rgt == std::string::npos) break;
        if (xml[rgt - 1] == '/') { pos = rgt + 1; continue; }
        size_t r1 = xml.find("</row>", rgt + 1);
        if (r1 == std::string::npos) break;

        std::string row_xml = xml.substr(rgt + 1, r1 - rgt - 1);
        std::vector<std::string> row;
        size_t cpos = 0;
        int sequential_col = 0;
        while (true) {
            size_t c0 = row_xml.find("<c", cpos);
            if (c0 == std::string::npos) break;
            char cnxt = (c0 + 2 < row_xml.size()) ? row_xml[c0 + 2] : '\0';
            if (cnxt != '>' && cnxt != ' ' && cnxt != '/') { cpos = c0 + 2; continue; }
            size_t cgt = row_xml.find('>', c0);
            if (cgt == std::string::npos) break;
            std::string attrs = row_xml.substr(c0, cgt - c0);
            std::string ref = extract_attr(attrs, "r");
            std::string type = extract_attr(attrs, "t");
            int col = col_index_from_ref(ref);
            if (col < 0) col = sequential_col;
            sequential_col = col + 1;

            if (row_xml[cgt - 1] == '/') {
                cpos = cgt + 1;
                continue;
            }
            size_t c1 = row_xml.find("</c>", cgt + 1);
            if (c1 == std::string::npos) break;
            std::string cell_xml = row_xml.substr(cgt + 1, c1 - cgt - 1);

            std::string val;
            if (type == "inlineStr") {
                val = collect_t_tags(cell_xml);
            } else {
                size_t v0 = cell_xml.find("<v>");
                size_t v1 = (v0 != std::string::npos) ? cell_xml.find("</v>", v0 + 3) : std::string::npos;
                if (v0 != std::string::npos && v1 != std::string::npos) {
                    std::string raw_v = unescape_xml(cell_xml.substr(v0 + 3, v1 - (v0 + 3)));
                    if (type == "s") {
                        int idx = std::atoi(raw_v.c_str());
                        if (idx >= 0 && idx < static_cast<int>(sst.size())) val = sst[idx];
                    } else {
                        val = raw_v;
                    }
                }
            }
            if (col >= static_cast<int>(row.size())) {
                row.resize(col + 1);
            }
            row[col] = trim(val);
            cpos = c1 + 4;
        }
        bool any = false;
        for (const auto& cell : row) {
            if (!cell.empty()) { any = true; break; }
        }
        if (any) rows.push_back(std::move(row));
        pos = r1 + 6;
    }
    return rows;
}

struct ColumnMap {
    int id = -1;
    int x = -1;
    int y = -1;
    int z = -1;
    int design_z = -1;
};

ColumnMap detect_columns(const std::vector<std::string>& header) {
    ColumnMap m;
    for (int i = 0; i < static_cast<int>(header.size()); ++i) {
        std::string h = to_lower_ascii(trim(header[i]));
        if (m.id < 0 && (h == "点号" || h == "点名" || h == "id" || h == "pid" || h == "point" || h == "编号" || h == "name")) {
            m.id = i;
        } else if (m.x < 0 && (h == "x" || h == "x坐标" || h == "横坐标" || h == "东向" || h == "easting" || h == "x(m)")) {
            m.x = i;
        } else if (m.y < 0 && (h == "y" || h == "y坐标" || h == "纵坐标" || h == "北向" || h == "northing" || h == "y(m)")) {
            m.y = i;
        } else if (m.z < 0 && (h == "z" || h == "实测高程" || h == "高程" || h == "elevation" || h == "h")) {
            m.z = i;
        } else if (m.design_z < 0 && (h == "设计高程" || h == "设计标高" || h == "design_z")) {
            m.design_z = i;
        }
    }
    std::vector<bool> used(header.size(), false);
    for (int idx : {m.id, m.x, m.y, m.z, m.design_z}) {
        if (idx >= 0 && idx < static_cast<int>(used.size())) used[idx] = true;
    }
    std::vector<int> unused;
    for (int i = 0; i < static_cast<int>(header.size()); ++i) {
        if (!used[i]) unused.push_back(i);
    }
    int need = (m.x < 0) + (m.y < 0) + (m.z < 0);
    if (m.id < 0 && static_cast<int>(unused.size()) > need) {
        m.id = unused.front();
        unused.erase(unused.begin());
    }
    auto pop_unused = [&]() -> int {
        if (unused.empty()) return -1;
        int v = unused.front();
        unused.erase(unused.begin());
        return v;
    };
    if (m.x < 0) m.x = pop_unused();
    if (m.y < 0) m.y = pop_unused();
    if (m.z < 0) m.z = pop_unused();
    return m;
}

ImportPointsResult parse_table_rows(const std::vector<std::vector<std::string>>& rows, const std::string& fmt) {
    if (rows.empty()) throw std::runtime_error("数据表为空");
    ColumnMap cmap = detect_columns(rows[0]);
    if (cmap.x < 0 || cmap.y < 0 || cmap.z < 0) {
        throw std::runtime_error("文件列数不足，至少需要 X、Y、高程 3 列");
    }

    ImportPointsResult out;
    out.detected_format = fmt;
    for (size_t r = 1; r < rows.size(); ++r) {
        const auto& row = rows[r];
        auto get = [&](int col) -> std::string {
            return (col >= 0 && col < static_cast<int>(row.size())) ? row[col] : "";
        };
        double x = 0.0, y = 0.0, z = 0.0;
        if (!parse_double_strict(get(cmap.x), x) ||
            !parse_double_strict(get(cmap.y), y) ||
            !parse_double_strict(get(cmap.z), z)) {
            out.issues.format_errors.push_back("第" + std::to_string(r) + "行: 坐标或高程为空或无法解析，已跳过");
            continue;
        }
        std::string pid = get(cmap.id);
        if (pid.empty()) pid = "P" + std::to_string(r);

        double dz = 0.0;
        bool has_dz = false;
        std::string dzs = get(cmap.design_z);
        if (!dzs.empty() && parse_double_strict(dzs, dz)) {
            has_dz = true;
        }
        out.points.push_back(SurveyPoint{pid, x, y, z, dz, has_dz ? (z - dz) : 0.0, has_dz});
    }
    if (out.points.empty()) {
        throw std::runtime_error("没有解析到有效测量点");
    }
    auto val = validate_points(out.points);
    val.format_errors.insert(val.format_errors.end(),
                             out.issues.format_errors.begin(), out.issues.format_errors.end());
    out.issues = std::move(val);
    return out;
}

bool looks_like_header(const std::vector<std::string>& fields) {
    if (fields.empty()) return false;
    std::string joined;
    for (const auto& f : fields) joined += to_lower_ascii(f);
    for (const char* kw : {"点号", "点名", "编号", "实测", "高程", "坐标", "northing", "easting"}) {
        if (joined.find(kw) != std::string::npos) return true;
    }
    double dummy = 0.0;
    if (fields.size() >= 5 && parse_double_strict(fields[2], dummy) &&
        parse_double_strict(fields[3], dummy) && parse_double_strict(fields[4], dummy)) {
        return false;
    }
    if (fields.size() >= 4 && parse_double_strict(fields[1], dummy) &&
        parse_double_strict(fields[2], dummy) && parse_double_strict(fields.back(), dummy)) {
        return false;
    }
    if (fields.size() == 3 && parse_double_strict(fields[0], dummy) &&
        parse_double_strict(fields[1], dummy) && parse_double_strict(fields[2], dummy)) {
        return false;
    }
    return true;
}

bool looks_like_cass(const std::vector<std::string>& fields) {
    if (fields.size() < 5) return false;
    double d1, d2, d3, d4;
    if (!parse_double_strict(fields[2], d2) ||
        !parse_double_strict(fields[3], d3) ||
        !parse_double_strict(fields[4], d4)) {
        return false;
    }
    return !parse_double_strict(fields[1], d1);
}

ImportPointsResult import_xlsx_bytes(const std::vector<std::uint8_t>& bytes) {
    mz_zip_archive zip{};
    if (!mz_zip_reader_init_mem(&zip, bytes.data(), bytes.size(), 0)) {
        throw std::runtime_error("无效的 .xlsx 压缩包结构");
    }
    auto extract_str = [&](const char* name) -> std::string {
        size_t sz = 0;
        void* ptr = mz_zip_reader_extract_file_to_heap(&zip, name, &sz, 0);
        if (!ptr) return {};
        std::string s(static_cast<const char*>(ptr), sz);
        mz_free(ptr);
        return s;
    };

    std::vector<std::string> sst;
    std::string sst_xml = extract_str("xl/sharedStrings.xml");
    if (!sst_xml.empty()) sst = parse_shared_strings(sst_xml);

    std::string sheet_xml = extract_str("xl/worksheets/sheet1.xml");
    if (sheet_xml.empty()) {
        mz_uint nf = mz_zip_reader_get_num_files(&zip);
        for (mz_uint i = 0; i < nf; ++i) {
            mz_zip_archive_file_stat st{};
            if (mz_zip_reader_file_stat(&zip, i, &st)) {
                std::string fname(st.m_filename);
                if (fname.rfind("xl/worksheets/sheet", 0) == 0 && fname.size() > 4 &&
                    fname.substr(fname.size() - 4) == ".xml") {
                    sheet_xml = extract_str(st.m_filename);
                    break;
                }
            }
        }
    }
    mz_zip_reader_end(&zip);

    if (sheet_xml.empty()) {
        throw std::runtime_error(".xlsx 中未找到工作表 XML");
    }
    auto rows = parse_sheet_rows(sheet_xml, sst);
    return parse_table_rows(rows, "Excel (.xlsx)");
}

struct DxfPolyline {
    std::string layer;
    bool closed = false;
    std::vector<SurveyPoint> pts;
};

std::vector<DxfPolyline> parse_dxf_polylines(const std::string& text) {
    std::istringstream ss(text);
    std::vector<std::pair<int, std::string>> pairs;
    std::string code_line, val_line;
    while (std::getline(ss, code_line)) {
        if (!std::getline(ss, val_line)) break;
        int code = std::atoi(trim(code_line).c_str());
        pairs.emplace_back(code, trim(val_line));
    }

    std::vector<DxfPolyline> out;
    size_t i = 0;
    while (i < pairs.size()) {
        if (pairs[i].first == 0 && pairs[i].second == "LWPOLYLINE") {
            DxfPolyline poly;
            double cur_x = 0.0, cur_y = 0.0, cur_elev = 0.0;
            bool has_x = false, has_y = false;
            ++i;
            while (i < pairs.size() && pairs[i].first != 0) {
                int c = pairs[i].first;
                const std::string& v = pairs[i].second;
                if (c == 8) poly.layer = v;
                else if (c == 70) poly.closed = (std::atoi(v.c_str()) & 1) != 0;
                else if (c == 38) parse_double_strict(v, cur_elev);
                else if (c == 10) {
                    if (has_x && has_y) {
                        poly.pts.push_back(SurveyPoint{"", cur_x, cur_y, cur_elev});
                    }
                    has_x = parse_double_strict(v, cur_x);
                    has_y = false;
                } else if (c == 20) {
                    has_y = parse_double_strict(v, cur_y);
                }
                ++i;
            }
            if (has_x && has_y) {
                poly.pts.push_back(SurveyPoint{"", cur_x, cur_y, cur_elev});
            }
            if (poly.pts.size() >= 2) out.push_back(std::move(poly));
            continue;
        }
        if (pairs[i].first == 0 && pairs[i].second == "POLYLINE") {
            DxfPolyline poly;
            ++i;
            while (i < pairs.size() && pairs[i].first != 0) {
                if (pairs[i].first == 8) poly.layer = pairs[i].second;
                else if (pairs[i].first == 70) poly.closed = (std::atoi(pairs[i].second.c_str()) & 1) != 0;
                ++i;
            }
            while (i < pairs.size()) {
                if (pairs[i].first == 0 && pairs[i].second == "SEQEND") {
                    ++i;
                    break;
                }
                if (pairs[i].first == 0 && pairs[i].second == "VERTEX") {
                    double vx = 0.0, vy = 0.0, vz = 0.0;
                    bool hx = false, hy = false;
                    ++i;
                    while (i < pairs.size() && pairs[i].first != 0) {
                        int c = pairs[i].first;
                        if (c == 10) hx = parse_double_strict(pairs[i].second, vx);
                        else if (c == 20) hy = parse_double_strict(pairs[i].second, vy);
                        else if (c == 30) parse_double_strict(pairs[i].second, vz);
                        ++i;
                    }
                    if (hx && hy) poly.pts.push_back(SurveyPoint{"", vx, vy, vz});
                    continue;
                }
                ++i;
            }
            if (poly.pts.size() >= 2) out.push_back(std::move(poly));
            continue;
        }
        ++i;
    }
    return out;
}

std::string fmt3(double v) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(3) << v;
    return os.str();
}

} // namespace

ValidationIssues validate_points(const std::vector<SurveyPoint>& points) {
    ValidationIssues issues;
    if (points.empty()) {
        issues.format_errors.push_back("没有数据");
        return issues;
    }

    std::map<std::pair<long long, long long>, int> coords_seen;
    std::map<std::string, int> ids_seen;
    double sx = 0.0, sy = 0.0, sz = 0.0;
    int valid_count = 0;
    for (const auto& p : points) {
        if (std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z)) {
            sx += p.x;
            sy += p.y;
            sz += p.z;
            ++valid_count;
        }
    }
    double mx = valid_count ? sx / valid_count : 0.0;
    double my = valid_count ? sy / valid_count : 0.0;
    double mz = valid_count ? sz / valid_count : 0.0;
    double vx = 0.0, vy = 0.0, vz = 0.0;
    for (const auto& p : points) {
        if (std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z)) {
            vx += (p.x - mx) * (p.x - mx);
            vy += (p.y - my) * (p.y - my);
            vz += (p.z - mz) * (p.z - mz);
        }
    }
    double stdx = valid_count ? std::sqrt(vx / valid_count) : 0.0;
    double stdy = valid_count ? std::sqrt(vy / valid_count) : 0.0;
    double stdz = valid_count ? std::sqrt(vz / valid_count) : 0.0;

    for (size_t i = 0; i < points.size(); ++i) {
        const auto& pt = points[i];
        if (!std::isfinite(pt.x) || !std::isfinite(pt.y) || !std::isfinite(pt.z)) {
            issues.missing_values.push_back("第" + std::to_string(i + 1) + "行: 坐标或高程缺失");
            continue;
        }
        auto key = std::make_pair(
            static_cast<long long>(std::llround(pt.x * 1000.0)),
            static_cast<long long>(std::llround(pt.y * 1000.0)));
        auto cit = coords_seen.find(key);
        if (cit != coords_seen.end()) {
            issues.duplicate_coords.push_back(
                "第" + std::to_string(i + 1) + "行与第" + std::to_string(cit->second + 1) + "行坐标重复");
        } else {
            coords_seen[key] = static_cast<int>(i);
        }
        auto iit = ids_seen.find(pt.id);
        if (iit != ids_seen.end()) {
            issues.duplicate_ids.push_back("第" + std::to_string(i + 1) + "行点号重复: " + pt.id);
        } else {
            ids_seen[pt.id] = static_cast<int>(i);
        }
        if (stdx > 0.0 && std::abs(pt.x - mx) > 3.0 * stdx) {
            issues.coord_outliers.push_back("第" + std::to_string(i + 1) + "行 X坐标异常: " + fmt3(pt.x));
        }
        if (stdy > 0.0 && std::abs(pt.y - my) > 3.0 * stdy) {
            issues.coord_outliers.push_back("第" + std::to_string(i + 1) + "行 Y坐标异常: " + fmt3(pt.y));
        }
        if (stdz > 0.0 && std::abs(pt.z - mz) > 3.0 * stdz) {
            issues.elevation_outliers.push_back("第" + std::to_string(i + 1) + "行 高程异常: " + fmt3(pt.z));
        }
    }
    return issues;
}

ImportPointsResult import_points_file(const std::string& utf8_path) {
    std::string ext = file_extension_lower(utf8_path);
    auto bytes = read_file_bytes(utf8_path);

    if (ext == ".xlsx") {
        return import_xlsx_bytes(bytes);
    }
    if (ext != ".csv" && ext != ".txt" && ext != ".dat") {
        throw std::runtime_error("不支持的文件格式: " + ext + "（支持 .xlsx / .csv / .txt / .dat）");
    }

    std::string text = decode_text_auto(bytes);
    std::vector<std::string> lines;
    std::istringstream ss(text);
    std::string raw;
    while (std::getline(ss, raw)) {
        std::string line = trim(raw);
        if (line.empty()) continue;
        if (line[0] == '#' || line[0] == '*' || line.rfind("//", 0) == 0) continue;
        std::string up = line;
        for (char& ch : up) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
        if (up == "BEGIN" || up == "END") continue;
        lines.push_back(std::move(line));
    }
    if (lines.empty()) throw std::runtime_error("文件为空");

    auto first = split_line_fields(lines[0]);
    bool has_header = looks_like_header(first);
    size_t start_row = has_header ? 1 : 0;
    if (start_row >= lines.size()) throw std::runtime_error("文件没有有效数据行");

    auto sample = split_line_fields(lines[start_row]);
    bool use_cass = (ext == ".dat") || looks_like_cass(sample);

    if (has_header && !use_cass) {
        std::vector<std::vector<std::string>> rows;
        rows.push_back(first);
        for (size_t i = start_row; i < lines.size(); ++i) {
            rows.push_back(split_line_fields(lines[i]));
        }
        return parse_table_rows(rows, ext == ".csv" ? "CSV 表格" : "文本表格");
    }

    ImportPointsResult out;
    out.detected_format = use_cass ? "南方CASS (.dat)" : "纯文本坐标";
    for (size_t i = start_row; i < lines.size(); ++i) {
        auto f = split_line_fields(lines[i]);
        double x = 0.0, y = 0.0, z = 0.0;
        std::string pid;
        bool ok = false;
        if (use_cass) {
            if (f.size() >= 5 && parse_double_strict(f[2], x) &&
                parse_double_strict(f[3], y) && parse_double_strict(f[4], z)) {
                pid = f[0].empty() ? ("P" + std::to_string(i + 1)) : f[0];
                ok = true;
            } else if (f.size() == 4 && parse_double_strict(f[1], x) &&
                       parse_double_strict(f[2], y) && parse_double_strict(f[3], z)) {
                pid = f[0].empty() ? ("P" + std::to_string(i + 1)) : f[0];
                ok = true;
            } else if (f.size() == 3 && parse_double_strict(f[0], x) &&
                       parse_double_strict(f[1], y) && parse_double_strict(f[2], z)) {
                pid = "P" + std::to_string(i + 1);
                ok = true;
            }
        } else {
            if (f.size() >= 4 && parse_double_strict(f[1], x) &&
                parse_double_strict(f[2], y) && parse_double_strict(f[3], z)) {
                pid = f[0].empty() ? ("P" + std::to_string(i + 1)) : f[0];
                ok = true;
            } else if (f.size() == 3 && parse_double_strict(f[0], x) &&
                       parse_double_strict(f[1], y) && parse_double_strict(f[2], z)) {
                pid = "P" + std::to_string(i + 1);
                ok = true;
            }
        }
        if (!ok) {
            out.issues.format_errors.push_back("第" + std::to_string(i + 1) + "行: 无法解析坐标或高程");
            continue;
        }
        out.points.push_back(SurveyPoint{pid, x, y, z});
    }
    if (out.points.empty()) throw std::runtime_error("没有可解析的测量点");
    auto val = validate_points(out.points);
    val.format_errors.insert(val.format_errors.end(),
                             out.issues.format_errors.begin(), out.issues.format_errors.end());
    out.issues = std::move(val);
    return out;
}

std::vector<XY> import_boundary_file(const std::string& utf8_path) {
    std::string ext = file_extension_lower(utf8_path);
    std::string text = decode_text_auto(read_file_bytes(utf8_path));
    if (ext == ".dxf") {
        auto polys = parse_dxf_polylines(text);
        if (polys.empty()) throw std::runtime_error("DXF 中未找到多段线 (LWPOLYLINE / POLYLINE)");
        auto best_it = polys.begin();
        double best_score = -1.0;
        for (auto it = polys.begin(); it != polys.end(); ++it) {
            if (it->pts.size() < 3) continue;
            std::vector<XY> ring;
            for (const auto& p : it->pts) ring.push_back({p.x, p.y});
            double area = std::abs(signed_area(ring));
            std::string llow = to_lower_ascii(it->layer);
            double bonus = (llow.find("boundary") != std::string::npos ||
                            it->layer.find("边界") != std::string::npos ||
                            it->layer.find("红线") != std::string::npos ||
                            llow.find("jzx") != std::string::npos) ? 1e18 : 0.0;
            if (bonus + area > best_score) {
                best_score = bonus + area;
                best_it = it;
            }
        }
        std::vector<XY> out;
        for (const auto& p : best_it->pts) out.push_back({p.x, p.y});
        if (out.size() >= 2 && out.front() == out.back()) out.pop_back();
        if (out.size() < 3) throw std::runtime_error("选中的 DXF 多段线顶点数不足 3 个");
        return out;
    }

    std::vector<XY> boundary;
    std::istringstream ss(text);
    std::string raw;
    while (std::getline(ss, raw)) {
        std::string line = trim(raw);
        if (line.empty()) continue;
        auto f = split_line_fields(line);
        double x = 0.0, y = 0.0;
        if (f.size() >= 2 && parse_double_strict(f[0], x) && parse_double_strict(f[1], y)) {
            boundary.push_back({x, y});
        } else if (f.size() >= 3 && parse_double_strict(f[1], x) && parse_double_strict(f[2], y)) {
            boundary.push_back({x, y});
        }
    }
    if (boundary.size() >= 2 && boundary.front() == boundary.back()) boundary.pop_back();
    if (boundary.size() < 3) throw std::runtime_error("边界至少需要 3 个有效坐标点");
    return boundary;
}

std::vector<Breakline> import_breaklines_dxf(const std::string& utf8_path) {
    std::string text = decode_text_auto(read_file_bytes(utf8_path));
    auto polys = parse_dxf_polylines(text);
    std::vector<Breakline> out;
    for (size_t i = 0; i < polys.size(); ++i) {
        if (polys[i].pts.size() < 2) continue;
        Breakline bl;
        bl.name = polys[i].layer.empty() ? ("DXF-" + std::to_string(i + 1)) : polys[i].layer;
        bl.points = std::move(polys[i].pts);
        bl.closed = polys[i].closed;
        out.push_back(std::move(bl));
    }
    return out;
}

namespace {

json points_to_json(const std::vector<SurveyPoint>& pts) {
    json arr = json::array();
    for (const auto& p : pts) {
        arr.push_back({
            {"id", p.id},
            {"x", p.x},
            {"y", p.y},
            {"z", p.z},
            {"design_z", p.design_z},
            {"has_design_z", p.has_design_z}
        });
    }
    return arr;
}

std::vector<SurveyPoint> points_from_json(const json& arr) {
    std::vector<SurveyPoint> pts;
    if (!arr.is_array()) return pts;
    for (const auto& row : arr) {
        double z = row.value("z", 0.0);
        double dz = row.value("design_z", 0.0);
        bool has_dz = row.value("has_design_z", row.contains("design_z"));
        pts.push_back(SurveyPoint{
            row.value("id", std::string("P")),
            row.value("x", 0.0),
            row.value("y", 0.0),
            z,
            dz,
            has_dz ? (z - dz) : 0.0,
            has_dz
        });
    }
    return pts;
}

} // namespace

ProjectData load_project_json(const std::string& utf8_path) {
    std::string text = decode_text_auto(read_file_bytes(utf8_path));
    json j = json::parse(text);
    if (j.contains("format") && !j["format"].is_null() && j["format"] != "tin-earthwork-project") {
        throw std::runtime_error("不是有效的 TIN 土方工程文件");
    }
    ProjectData p;
    p.project_name = j.value("project_name", std::string("TIN土方计算项目"));
    p.points = points_from_json(j.value("points", json::array()));
    p.compare_points = points_from_json(j.value("compare_points", json::array()));
    p.compare_source = j.value("compare_source", std::string(""));

    if (j.contains("boundary") && j["boundary"].is_array()) {
        for (const auto& pt : j["boundary"]) {
            if (pt.is_array() && pt.size() >= 2) {
                p.boundary.push_back({pt[0].get<double>(), pt[1].get<double>()});
            }
        }
    }
    if (j.contains("holes") && j["holes"].is_array()) {
        for (const auto& ring : j["holes"]) {
            if (!ring.is_array()) continue;
            std::vector<XY> poly;
            for (const auto& pt : ring) {
                if (pt.is_array() && pt.size() >= 2) {
                    poly.push_back({pt[0].get<double>(), pt[1].get<double>()});
                }
            }
            if (poly.size() >= 3) p.holes.push_back(std::move(poly));
        }
    }
    if (j.contains("breaklines") && j["breaklines"].is_array()) {
        for (const auto& blj : j["breaklines"]) {
            Breakline bl;
            bl.name = blj.value("name", std::string("特征线"));
            bl.closed = blj.value("closed", false);
            if (blj.contains("points") && blj["points"].is_array()) {
                for (const auto& pt : blj["points"]) {
                    if (pt.is_array() && pt.size() >= 3) {
                        bl.points.push_back(SurveyPoint{"", pt[0].get<double>(), pt[1].get<double>(), pt[2].get<double>()});
                    }
                }
            }
            if (bl.points.size() >= 2) p.breaklines.push_back(std::move(bl));
        }
    }

    std::string mode = j.value("design_mode", std::string("flat"));
    if (mode == "plane") p.settings.design_mode = DesignMode::Plane;
    else if (mode == "compare") p.settings.design_mode = DesignMode::Compare;
    else p.settings.design_mode = DesignMode::Flat;

    p.settings.design_elevation = j.value("design_elevation", 0.0);
    if (j.contains("design_plane") && j["design_plane"].is_object()) {
        const auto& dp = j["design_plane"];
        p.settings.design_plane = PlaneDesign{
            dp.value("x0", 0.0), dp.value("y0", 0.0), dp.value("h0", 0.0),
            dp.value("slope_x", 0.0), dp.value("slope_y", 0.0)
        };
    }
    p.settings.use_partition = j.value("use_partition", false);
    if (j.contains("partition") && j["partition"].is_object()) {
        for (auto it = j["partition"].begin(); it != j["partition"].end(); ++it) {
            p.settings.partition[it.key()] = it.value().get<double>();
        }
    }
    if (j.contains("grid_check") && j["grid_check"].is_object()) {
        const auto& gc = j["grid_check"];
        p.settings.grid_enabled = gc.value("enabled", false);
        if (gc.contains("spacing") && gc["spacing"].is_number()) {
            p.settings.grid_spacing = gc["spacing"].get<double>();
        }
    }
    if (j.contains("contours") && j["contours"].is_object()) {
        const auto& ct = j["contours"];
        p.settings.contours_enabled = ct.value("enabled", true);
        if (ct.contains("interval") && ct["interval"].is_number()) {
            p.settings.contour_interval = ct["interval"].get<double>();
        }
    }
    if (j.contains("max_edge_length") && j["max_edge_length"].is_number()) {
        p.settings.max_edge_length = j["max_edge_length"].get<double>();
    }
    if (j.contains("soil_factors") && j["soil_factors"].is_object()) {
        const auto& sf = j["soil_factors"];
        p.settings.soil_factors.loose = sf.value("loose", 1.0);
        p.settings.soil_factors.compact = sf.value("compact", 1.0);
    }
    return p;
}

void save_project_json(const std::string& utf8_path, const ProjectData& project) {
    std::string mode_str = "flat";
    if (project.settings.design_mode == DesignMode::Plane) mode_str = "plane";
    else if (project.settings.design_mode == DesignMode::Compare) mode_str = "compare";

    json boundary_j = json::array();
    for (const auto& pt : project.boundary) boundary_j.push_back({pt.x, pt.y});

    json holes_j = json::array();
    for (const auto& h : project.holes) {
        json ring = json::array();
        for (const auto& pt : h) ring.push_back({pt.x, pt.y});
        holes_j.push_back(std::move(ring));
    }

    json bl_j = json::array();
    for (const auto& bl : project.breaklines) {
        json pts = json::array();
        for (const auto& pt : bl.points) pts.push_back({pt.x, pt.y, pt.z});
        bl_j.push_back({{"name", bl.name}, {"closed", bl.closed}, {"points", std::move(pts)}});
    }

    json part_j = json::object();
    for (const auto& kv : project.settings.partition) {
        part_j[kv.first] = kv.second;
    }

    json payload = {
        {"format", "tin-earthwork-project"},
        {"format_version", 3},
        {"app_version", "2.1.0-cpp"},
        {"project_name", project.project_name},
        {"design_mode", mode_str},
        {"design_elevation", project.settings.design_elevation},
        {"design_plane", {
            {"x0", project.settings.design_plane.x0},
            {"y0", project.settings.design_plane.y0},
            {"h0", project.settings.design_plane.h0},
            {"slope_x", project.settings.design_plane.slope_x},
            {"slope_y", project.settings.design_plane.slope_y}
        }},
        {"use_partition", project.settings.use_partition},
        {"partition", part_j},
        {"grid_check", {{"enabled", project.settings.grid_enabled}, {"spacing", project.settings.grid_spacing}}},
        {"contours", {{"enabled", project.settings.contours_enabled},
                      {"interval", project.settings.contour_interval > 0 ? json(project.settings.contour_interval) : json(nullptr)}}},
        {"max_edge_length", project.settings.max_edge_length > 0 ? json(project.settings.max_edge_length) : json(nullptr)},
        {"soil_factors", {{"loose", project.settings.soil_factors.loose}, {"compact", project.settings.soil_factors.compact}}},
        {"compare_source", project.compare_source},
        {"boundary", boundary_j},
        {"holes", holes_j},
        {"breaklines", bl_j},
        {"points", points_to_json(project.points)},
        {"compare_points", points_to_json(project.compare_points)}
    };
    write_utf8_text(utf8_path, payload.dump(2), false);
}

void export_summary_csv(const CalculationResult& result, const std::string& utf8_path) {
    std::ostringstream os;
    os << "项目,数值,单位,说明\n";
    os << "总挖方量," << fmt3(result.total_cut) << ",m³,高于比较面部分\n";
    os << "总填方量," << fmt3(result.total_fill) << ",m³,低于比较面部分\n";
    os << "挖填净值," << fmt3(result.net_volume) << ",m³,正为净挖、负为净填\n";
    os << "有效计算面积," << fmt3(result.computed_area) << ",m²,参与计算的水平面积\n";
    if (result.boundary_area > 0.0) {
        os << "计算边界面积," << fmt3(result.boundary_area) << ",m²,已扣除挖空区\n";
    }
    os << "三角形总数," << result.triangle_count << ",个,参与计算的 TIN 单元\n";
    os << "混合挖填三角形," << result.mixed_triangle_count << ",个,按零填挖线精确分割\n";
    os << "比较面,\"" << result.design_description << "\",,\n";
    if (result.grid_check.has_value()) {
        const auto& g = *result.grid_check;
        os << "方格网法挖方," << fmt3(g.total_cut) << ",m³,边长 " << g.spacing << " m\n";
        os << "方格网法填方," << fmt3(g.total_fill) << ",m³,边长 " << g.spacing << " m\n";
        os << "方格网法净方量," << fmt3(g.net_volume()) << ",m³,与TIN法差 " << fmt3(g.net_volume() - result.net_volume) << " m³\n";
    }
    write_utf8_text(utf8_path, os.str(), true);
}

void export_detail_csv(const CalculationResult& result, const std::string& utf8_path) {
    std::ostringstream os;
    os << "三角形编号,顶点1,顶点2,顶点3,边界内面积(m²),平均高差(m),挖方量(m³),填方量(m³),净体积(m³),类型\n";
    for (const auto& tri : result.triangles) {
        if (tri.is_boundary) continue;
        std::string kind = tri.is_mixed ? "混合" : (tri.volume >= 0.0 ? "挖方" : "填方");
        if (tri.is_clipped) kind += "(裁剪)";
        os << tri.id << ","
           << tri.p0.id << "," << tri.p1.id << "," << tri.p2.id << ","
           << fmt3(tri.area) << "," << fmt3(tri.avg_delta_z) << ","
           << fmt3(tri.cut_volume) << "," << fmt3(tri.fill_volume) << ","
           << fmt3(tri.volume) << "," << kind << "\n";
    }
    write_utf8_text(utf8_path, os.str(), true);
}

void export_report_txt(const CalculationResult& result, int point_count,
                       const std::string& project_name, const std::string& utf8_path) {
    std::time_t now = std::time(nullptr);
    std::tm tm_now{};
#ifdef _WIN32
    localtime_s(&tm_now, &now);
#else
    localtime_r(&now, &tm_now);
#endif
    char tbuf[64]{};
    std::strftime(tbuf, sizeof(tbuf), "%Y-%m-%d %H:%M:%S", &tm_now);

    std::ostringstream os;
    os << "============================================================\n";
    os << "          " << project_name << " - TIN 土方计算报告 (C++ CAD版)\n";
    os << "============================================================\n\n";
    os << "【工程基本信息】\n";
    os << "计算时间: " << tbuf << "\n";
    os << "测点总数: " << point_count << "\n";
    os << "参与计算三角形数: " << result.triangle_count
       << "（其中混合挖填 " << result.mixed_triangle_count
       << " 个，边界裁剪 " << result.clipped_triangle_count << " 个）\n";
    os << "比较面说明: " << result.design_description << "\n";
    os << "有效计算面积: " << fmt3(result.computed_area) << " m²\n";
    if (result.boundary_area > 0.0) {
        os << "计算边界面积: " << fmt3(result.boundary_area) << " m²\n";
    }
    os << "\n【挖填方量汇总】\n";
    os << "总挖方量: " << fmt3(result.total_cut) << " m³\n";
    os << "总填方量: " << fmt3(result.total_fill) << " m³\n";
    os << "挖填净值: " << fmt3(result.net_volume) << " m³（"
       << (result.net_volume >= 0.0 ? "需外运土方" : "需外借土方") << "）\n";
    if (!result.soil_balance.factors.is_default()) {
        os << "挖方松方量: " << fmt3(result.soil_balance.cut_loose()) << " m³\n";
        os << "填方所需自然方: " << fmt3(result.soil_balance.fill_natural()) << " m³\n";
    }
    if (result.grid_check.has_value()) {
        const auto& g = *result.grid_check;
        os << "\n【方格网法校核】\n";
        os << "方格边长: " << g.spacing << " m（有效方格 " << g.cells.size() << " 个）\n";
        os << "方格网挖方: " << fmt3(g.total_cut) << " m³（差值 " << fmt3(g.total_cut - result.total_cut) << " m³）\n";
        os << "方格网填方: " << fmt3(g.total_fill) << " m³（差值 " << fmt3(g.total_fill - result.total_fill) << " m³）\n";
    }
    if (!result.warnings.empty()) {
        os << "\n【工程提示】\n";
        for (const auto& w : result.warnings) os << "- " << w << "\n";
    }
    os << "\n============================================================\n";
    write_utf8_text(utf8_path, os.str(), false);
}

void export_dxf_r12(const CalculationResult& result, const std::string& utf8_path) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(4);

    auto write_polyline = [&](const std::vector<XY>& pts, const std::string& layer, int color, bool closed) {
        if (pts.size() < 2) return;
        os << "0\nPOLYLINE\n8\n" << layer << "\n62\n" << color << "\n66\n1\n70\n" << (closed ? 1 : 0) << "\n";
        for (const auto& p : pts) {
            os << "0\nVERTEX\n8\n" << layer << "\n10\n" << p.x << "\n20\n" << p.y << "\n30\n0.0\n";
        }
        os << "0\nSEQEND\n";
    };
    auto write_line = [&](const XY& a, const XY& b, const std::string& layer, int color) {
        os << "0\nLINE\n8\n" << layer << "\n62\n" << color
           << "\n10\n" << a.x << "\n20\n" << a.y << "\n30\n0.0"
           << "\n11\n" << b.x << "\n21\n" << b.y << "\n31\n0.0\n";
    };

    os << "0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1009\n0\nENDSEC\n";
    os << "0\nSECTION\n2\nENTITIES\n";

    if (result.boundary_points.size() >= 3) {
        write_polyline(result.boundary_points, "BOUNDARY", 4, true); // 青色边界
    }
    for (const auto& hole : result.holes) {
        write_polyline(hole, "HOLE", 1, true); // 红色挖空区
    }
    for (const auto& bl : result.breakline_polylines) {
        write_polyline(bl, "BREAKLINE", 5, false); // 蓝色特征线
    }
    for (const auto& tri : result.triangles) {
        if (tri.is_boundary) continue;
        XY a{tri.p0.x, tri.p0.y}, b{tri.p1.x, tri.p1.y}, c{tri.p2.x, tri.p2.y};
        write_line(a, b, "TIN", 8);
        write_line(b, c, "TIN", 8);
        write_line(c, a, "TIN", 8);
        for (const auto& zs : tri.zero_segments) {
            write_line(zs.first, zs.second, "ZERO_CONTOUR", 2); // 黄色零填挖线
        }
    }
    if (result.contours.has_value()) {
        for (const auto& line : result.contours->lines) {
            write_polyline(line.polyline,
                           line.major ? "CONTOUR_MAJOR" : "CONTOUR_MINOR",
                           line.major ? 30 : 34, false);
        }
    }
    if (result.grid_check.has_value()) {
        for (const auto& cell : result.grid_check->cells) {
            write_polyline(
                {{cell.x0, cell.y0}, {cell.x1, cell.y0}, {cell.x1, cell.y1}, {cell.x0, cell.y1}},
                "GRID", 4, true);
        }
    }
    os << "0\nENDSEC\n0\nEOF\n";
    write_utf8_text(utf8_path, os.str(), false);
}

void export_cass_dat(const std::vector<SurveyPoint>& points, const std::string& utf8_path, const std::string& code) {
    if (points.empty()) throw std::runtime_error("没有可导出的测量点");
    std::ostringstream os;
    os << std::fixed << std::setprecision(3);
    for (const auto& pt : points) {
        os << pt.id << "," << code << "," << pt.x << "," << pt.y << "," << pt.z << "\n";
    }
    write_utf8_text(utf8_path, os.str(), false);
}

void export_sections_csv(const SectionMethodResult& sections, const std::string& utf8_path) {
    if (sections.stations.empty()) throw std::runtime_error("没有可导出的平行断面数据");
    std::ostringstream os;
    os << std::fixed << std::setprecision(3);
    os << "桩号,里程(m),有效宽度(m),挖方面积(m²),填方面积(m²),最大挖深(m),最大填高(m),段间挖方(m³),段间填方(m³)\n";
    for (const auto& st : sections.stations) {
        os << st.name << ","
           << st.offset << ","
           << st.valid_width << ","
           << st.cut_area << ","
           << st.fill_area << ","
           << st.max_cut << ","
           << st.max_fill << ","
           << st.seg_cut_vol << ","
           << st.seg_fill_vol << "\n";
    }
    os << "合计," << sections.total_length << ",,,,,,"
       << sections.total_cut << "," << sections.total_fill << "\n";
    write_utf8_text(utf8_path, os.str(), true);
}

void export_survey_recommendations_csv(const UncertaintyReport& uncertainty, const std::string& utf8_path) {
    if (uncertainty.recommendations.empty()) {
        throw std::runtime_error("没有可导出的补测点推荐数据");
    }
    std::ostringstream os;
    os << std::fixed << std::setprecision(3);
    os << "序号,X(东坐标),Y(北坐标),预估高程Z(m),局部误差尺度(m3),预计降误差(m3),推荐原因\n";
    for (const auto& r : uncertainty.recommendations) {
        os << r.rank << "," << r.x << "," << r.y << "," << r.estimated_z << ","
           << r.error_scale << "," << r.expected_error_drop << ",\"" << r.reason << "\"\n";
    }
    write_utf8_text(utf8_path, os.str(), true);
}

void export_sensitivity_csv(const SensitivityCurveResult& curve, const std::string& utf8_path) {
    if (curve.points.empty()) {
        throw std::runtime_error("没有可导出的标高敏感性曲线数据");
    }
    std::ostringstream os;
    os << std::fixed << std::setprecision(3);
    os << "设计标高(m),挖方量(m³),填方量(m³),净方量(m³),估算综合费用(元)\n";
    for (const auto& p : curve.points) {
        os << p.elevation << "," << p.cut_volume << "," << p.fill_volume << ","
           << p.net_volume << "," << p.total_cost << "\n";
    }
    write_utf8_text(utf8_path, os.str(), true);
}

void export_ai_overlays_dxf(
    const CalculationResult* result,
    const SiteHealthReport* health,
    const UncertaintyReport* uncertainty,
    const HaulPlanResult* haul,
    const SlopeGradingResult* slope,
    const std::string& utf8_path) {
    std::ostringstream os;
    os << std::fixed << std::setprecision(4);
    os << "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
       << "0\nSECTION\n2\nENTITIES\n";

    auto emit_point_cross = [&](double x, double y, double z, const char* layer, int color, double r, const std::string& label) {
        os << "0\nLINE\n8\n" << layer << "\n62\n" << color << "\n"
           << "10\n" << (x - r) << "\n20\n" << (y - r) << "\n30\n" << z << "\n"
           << "11\n" << (x + r) << "\n21\n" << (y + r) << "\n31\n" << z << "\n";
        os << "0\nLINE\n8\n" << layer << "\n62\n" << color << "\n"
           << "10\n" << (x - r) << "\n20\n" << (y + r) << "\n30\n" << z << "\n"
           << "11\n" << (x + r) << "\n21\n" << (y - r) << "\n31\n" << z << "\n";
        os << "0\nCIRCLE\n8\n" << layer << "\n62\n" << color << "\n"
           << "10\n" << x << "\n20\n" << y << "\n30\n" << z << "\n40\n" << r << "\n";
        if (!label.empty()) {
            os << "0\nTEXT\n8\n" << layer << "\n62\n" << color << "\n"
               << "10\n" << (x + r * 1.2) << "\n20\n" << y << "\n30\n" << z << "\n"
               << "40\n" << std::max(0.8, r * 0.8) << "\n1\n" << label << "\n";
        }
    };

    if (result) {
        for (const auto& t : result->triangles) {
            if (t.is_boundary) continue;
            for (const auto& seg : t.zero_segments) {
                os << "0\nLINE\n8\nZERO_LINE\n62\n2\n"
                   << "10\n" << seg.first.x << "\n20\n" << seg.first.y << "\n30\n0.0\n"
                   << "11\n" << seg.second.x << "\n21\n" << seg.second.y << "\n31\n0.0\n";
            }
        }
    }
    if (health) {
        for (const auto& a : health->anomalies) {
            emit_point_cross(a.x, a.y, a.z, "AI_ANOMALY", 1, 2.5, a.point_id);
        }
    }
    if (uncertainty) {
        for (const auto& r : uncertainty->recommendations) {
            emit_point_cross(r.x, r.y, r.estimated_z, "AI_SURVEY_REC", 6, 2.2, "REC#" + std::to_string(r.rank));
        }
    }
    if (haul) {
        for (const auto& fl : haul->flows) {
            os << "0\nLINE\n8\nAI_HAUL_FLOW\n62\n3\n"
               << "10\n" << fl.from_xy.x << "\n20\n" << fl.from_xy.y << "\n30\n0.0\n"
               << "11\n" << fl.to_xy.x << "\n21\n" << fl.to_xy.y << "\n31\n0.0\n";
        }
    }
    if (slope && slope->daylight_ring.size() >= 3) {
        os << "0\nPOLYLINE\n8\nAI_SLOPE_DAYLIGHT\n62\n4\n66\n1\n70\n1\n";
        for (const auto& p : slope->daylight_ring) {
            os << "0\nVERTEX\n8\nAI_SLOPE_DAYLIGHT\n10\n" << p.x << "\n20\n" << p.y << "\n30\n0.0\n";
        }
        os << "0\nSEQEND\n";
        for (const auto& tk : slope->slope_ticks) {
            os << "0\nLINE\n8\nAI_SLOPE_DAYLIGHT\n62\n4\n"
               << "10\n" << tk.first.x << "\n20\n" << tk.first.y << "\n30\n0.0\n"
               << "11\n" << tk.second.x << "\n21\n" << tk.second.y << "\n31\n0.0\n";
        }
    }

    os << "0\nENDSEC\n0\nEOF\n";
    write_utf8_text(utf8_path, os.str(), false);
}

void export_ai_report_html(
    const ProjectData& project,
    const CalculationResult* result,
    const GradeOptimizationResult* grade_opt,
    const SiteHealthReport* health,
    const UncertaintyReport* uncertainty,
    const SignificanceReport* significance,
    const DrainageReport* drainage,
    const DiagnosisSummary* diagnosis,
    const std::string& utf8_path) {
    auto esc = [](const std::string& s) {
        std::string r;
        r.reserve(s.size());
        for (char c : s) {
            if (c == '&') r += "&amp;";
            else if (c == '<') r += "&lt;";
            else if (c == '>') r += "&gt;";
            else r.push_back(c);
        }
        return r;
    };

    std::ostringstream os;
    os << std::fixed << std::setprecision(2);
    os << "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
       << "<title>" << esc(project.project_name) << " — AI 土方决策报告</title>"
       << "<style>"
       << "body{font-family:-apple-system,'Microsoft YaHei',sans-serif;background:#f8fafc;color:#0f172a;margin:32px auto;max-width:980px;line-height:1.55;}"
       << "h1{border-bottom:3px solid #2563eb;padding-bottom:8px;font-size:24px;}"
       << "h2{margin-top:26px;font-size:17px;border-left:4px solid #2563eb;padding-left:10px;}"
       << ".grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:14px 0;}"
       << ".card{background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px 14px;}"
       << ".card .k{font-size:12px;color:#475569;}.card .v{font-size:20px;font-weight:700;margin-top:4px;}"
       << "table{width:100%;border-collapse:collapse;background:#fff;margin-top:8px;font-size:13px;}"
       << "th,td{border:1px solid #cbd5e1;padding:7px 10px;text-align:left;}"
       << "th{background:#e2e8f0;}"
       << ".badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;background:#dbeafe;color:#1e40af;}"
       << "@media print{body{margin:12px;background:#fff;}}"
       << "</style></head><body>";

    os << "<h1>" << esc(project.project_name) << " — 南方CASS AI 土方智能决策报告</h1>";
    os << "<p>测点总数: <b>" << project.points.size() << "</b>";
    if (health) os << " &nbsp;|&nbsp; 数据健康分: <span class=\"badge\">" << health->health_score << " / 100</span>";
    os << "</p>";

    if (result) {
        os << "<div class=\"grid\">"
           << "<div class=\"card\"><div class=\"k\">总挖方 (Cut)</div><div class=\"v\" style=\"color:#dc2626\">" << result->total_cut << " m³</div></div>"
           << "<div class=\"card\"><div class=\"k\">总填方 (Fill)</div><div class=\"v\" style=\"color:#0284c7\">" << result->total_fill << " m³</div></div>"
           << "<div class=\"card\"><div class=\"k\">挖填净方 (Net)</div><div class=\"v\">" << result->net_volume << " m³</div></div>"
           << "<div class=\"card\"><div class=\"k\">计算面积 (Area)</div><div class=\"v\">" << result->computed_area << " m²</div></div>"
           << "</div>";
    }

    if (grade_opt) {
        double max_c = std::max({grade_opt->flat_balance.total_cost, grade_opt->best_fit.total_cost, grade_opt->optimized.total_cost, 1.0});
        auto bar_w = [&](double c) { return 420.0 * (c / max_c); };
        os << "<h2>1. 设计面-土方调配联合费用优化对照</h2>"
           << "<svg width=\"660\" height=\"126\" viewBox=\"0 0 660 126\" xmlns=\"http://www.w3.org/2000/svg\" style=\"background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:6px\">"
           << "<text x=\"10\" y=\"28\" font-size=\"12\" fill=\"#334155\">平场平衡</text>"
           << "<rect x=\"120\" y=\"14\" width=\"" << bar_w(grade_opt->flat_balance.total_cost) << "\" height=\"20\" fill=\"#94a3b8\" rx=\"3\"/>"
           << "<text x=\"" << (128 + bar_w(grade_opt->flat_balance.total_cost)) << "\" y=\"28\" font-size=\"12\">¥" << grade_opt->flat_balance.total_cost << "</text>"
           << "<text x=\"10\" y=\"66\" font-size=\"12\" fill=\"#334155\">最小二乘</text>"
           << "<rect x=\"120\" y=\"52\" width=\"" << bar_w(grade_opt->best_fit.total_cost) << "\" height=\"20\" fill=\"#3b82f6\" rx=\"3\"/>"
           << "<text x=\"" << (128 + bar_w(grade_opt->best_fit.total_cost)) << "\" y=\"66\" font-size=\"12\">¥" << grade_opt->best_fit.total_cost << "</text>"
           << "<text x=\"10\" y=\"104\" font-size=\"12\" font-weight=\"700\" fill=\"#15803d\">AI 联合最优</text>"
           << "<rect x=\"120\" y=\"90\" width=\"" << bar_w(grade_opt->optimized.total_cost) << "\" height=\"20\" fill=\"#16a34a\" rx=\"3\"/>"
           << "<text x=\"" << (128 + bar_w(grade_opt->optimized.total_cost)) << "\" y=\"104\" font-size=\"12\" font-weight=\"700\">¥" << grade_opt->optimized.total_cost << " (省 " << grade_opt->savings_pct_vs_flat << "%)</text>"
           << "</svg>";

        os << "<table><tr><th>方案</th><th>H0 (m)</th><th>ix / iy (%)</th><th>挖方 (m³)</th><th>填方 (m³)</th><th>总费用 (元)</th></tr>";
        auto trow = [&](const GradePlanSummary& s) {
            os << "<tr><td>" << esc(s.label) << "</td><td>" << s.plane.h0 << "</td><td>"
               << s.plane.slope_x << " / " << s.plane.slope_y << "</td><td>"
               << s.cut_volume << "</td><td>" << s.fill_volume << "</td><td><b>" << s.total_cost << "</b></td></tr>";
        };
        trow(grade_opt->flat_balance);
        trow(grade_opt->best_fit);
        trow(grade_opt->optimized);
        os << "</table>";
    }

    if (health) {
        os << "<h2>2. 测点质量与空间异常诊断</h2><p>" << esc(health->summary) << "</p>";
        if (!health->anomalies.empty()) {
            os << "<table><tr><th>点号</th><th>X</th><th>Y</th><th>实测Z</th><th>预测Z</th><th>残差</th><th>说明</th></tr>";
            for (const auto& a : health->anomalies) {
                os << "<tr><td>" << esc(a.point_id) << "</td><td>" << a.x << "</td><td>" << a.y
                   << "</td><td>" << a.z << "</td><td>" << a.expected_z << "</td><td>" << a.residual
                   << "</td><td>" << esc(a.reason) << "</td></tr>";
            }
            os << "</table>";
        }
    }

    if (uncertainty) {
        os << "<h2>3. 不确定度评定与补测推荐</h2><p>" << esc(uncertainty->summary) << "</p>";
        if (!uncertainty->recommendations.empty()) {
            os << "<table><tr><th>序号</th><th>X</th><th>Y</th><th>预估 Z</th><th>预计降误差</th><th>推荐理由</th></tr>";
            for (const auto& r : uncertainty->recommendations) {
                os << "<tr><td>#" << r.rank << "</td><td>" << r.x << "</td><td>" << r.y
                   << "</td><td>" << r.estimated_z << "</td><td>" << r.expected_error_drop
                   << "</td><td>" << esc(r.reason) << "</td></tr>";
            }
            os << "</table>";
        }
    }

    if (significance || drainage) {
        os << "<h2>4. 变化显著性 LoD 与场地排水风险</h2>";
        if (significance) os << "<p><b>变化显著性：</b>" << esc(significance->summary) << "</p>";
        if (drainage) os << "<p><b>排水积水：</b>" << esc(drainage->summary) << "</p>";
    }

    if (diagnosis && !diagnosis->items.empty()) {
        os << "<h2>5. AI 工程综合建议</h2><ul>";
        for (const auto& it : diagnosis->items) {
            os << "<li><b>[" << esc(it.title) << "]</b> " << esc(it.detail) << "</li>";
        }
        os << "</ul>";
    }

    os << "</body></html>";
    write_utf8_text(utf8_path, os.str(), false);
}

void save_rgba_png(const std::uint8_t* rgba, int width, int height, const std::string& utf8_path) {
    size_t png_len = 0;
    void* png_buf = tdefl_write_image_to_png_file_in_memory_ex(
        rgba, width, height, 4, &png_len, MZ_DEFAULT_LEVEL, MZ_FALSE);
    if (!png_buf || png_len == 0) {
        throw std::runtime_error("PNG 图像编码失败");
    }
    try {
        write_file_bytes(utf8_path, png_buf, png_len);
    } catch (...) {
        mz_free(png_buf);
        throw;
    }
    mz_free(png_buf);
}

} // namespace tin
