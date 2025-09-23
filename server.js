const express = require('express');
const fs = require('fs');
const path = require('path');
const app = express();
const PORT = 3000;

// 中间件
app.use(express.json());
app.use(express.static('public'));

// 数据文件路径
const dataDir = path.join(__dirname, 'data');
const dataFile = path.join(dataDir, 'medicines.json');

// 确保数据目录存在
if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir);
}

// 初始化数据文件（如果不存在）
if (!fs.existsSync(dataFile)) {
    fs.writeFileSync(dataFile, JSON.stringify({
        medicines: [],
        usageRecords: []
    }, null, 2));
}

// 读取数据函数
function readData() {
    try {
        const data = fs.readFileSync(dataFile, 'utf8');
        return JSON.parse(data);
    } catch (error) {
        console.error('读取数据失败:', error);
        return { medicines: [], usageRecords: [] };
    }
}

// 保存数据函数
function saveData(data) {
    try {
        fs.writeFileSync(dataFile, JSON.stringify(data, null, 2));
        return true;
    } catch (error) {
        console.error('保存数据失败:', error);
        return false;
    }
}

// API 路由

// 获取所有数据
app.get('/api/data', (req, res) => {
    const data = readData();
    res.json(data);
});

// 保存所有数据
app.post('/api/data', (req, res) => {
    if (saveData(req.body)) {
        res.json({ success: true, message: '数据保存成功' });
    } else {
        res.status(500).json({ success: false, message: '数据保存失败' });
    }
});

// 启动服务器
app.listen(PORT, () => {
    console.log(`实验室药品管理系统已启动！`);
    console.log(`请打开浏览器访问: http://localhost:${PORT}`);
    console.log(`数据保存在: ${dataDir}`);
});