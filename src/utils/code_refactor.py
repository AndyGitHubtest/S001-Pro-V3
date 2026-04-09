"""
代码重构辅助工具
检测重复代码、优化建议
"""
import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class CodeAnalyzer:
    """
    代码分析器
    
    检测:
    1. 重复代码
    2. 未使用导入
    3. 复杂函数
    4. 代码异味
    """
    
    def __init__(self, src_dir: str = "src"):
        self.src_dir = Path(src_dir)
        self.issues: List[Dict] = []
    
    def analyze_project(self) -> Dict:
        """分析整个项目"""
        logger.info("="*60)
        logger.info("🔍 代码分析开始")
        logger.info("="*60)
        
        # 收集所有Python文件
        py_files = list(self.src_dir.rglob("*.py"))
        logger.info(f"发现 {len(py_files)} 个Python文件")
        
        # 分析各项指标
        self._find_duplicate_code(py_files)
        self._find_unused_imports(py_files)
        self._find_complex_functions(py_files)
        self._find_code_smells(py_files)
        
        report = {
            'total_files': len(py_files),
            'total_issues': len(self.issues),
            'issues_by_type': self._categorize_issues(),
            'issues': self.issues[:50]  # 最多显示50个
        }
        
        return report
    
    def _find_duplicate_code(self, py_files: List[Path]):
        """查找重复代码"""
        code_blocks = defaultdict(list)
        
        for file_path in py_files:
            try:
                content = file_path.read_text(encoding='utf-8')
                
                # 提取函数体 (简化版)
                functions = re.findall(r'def\s+\w+\s*\([^)]*\):\s*(.*?)(?=\ndef|\Z)', 
                                      content, re.DOTALL)
                
                for i, func_body in enumerate(functions):
                    # 标准化 (去除空格和注释)
                    normalized = re.sub(r'\s+', ' ', func_body.strip())
                    if len(normalized) > 100:  # 至少100字符
                        code_blocks[normalized[:200]].append((file_path, i))
                        
            except Exception as e:
                logger.error(f"分析文件失败 {file_path}: {e}")
        
        # 找出重复
        for code_hash, locations in code_blocks.items():
            if len(locations) > 1:
                self.issues.append({
                    'type': 'duplicate_code',
                    'severity': 'warning',
                    'message': f"发现重复代码块 ({len(locations)} 处)",
                    'locations': [str(loc[0]) for loc in locations]
                })
    
    def _find_unused_imports(self, py_files: List[Path]):
        """查找未使用的导入"""
        for file_path in py_files:
            try:
                content = file_path.read_text(encoding='utf-8')
                tree = ast.parse(content)
                
                # 收集导入
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ''
                        for alias in node.names:
                            imports.append(f"{module}.{alias.name}")
                
                # 检查是否使用 (简化检查)
                for imp in imports:
                    name = imp.split('.')[-1]
                    # 统计使用次数 (排除导入行本身)
                    usage = content.count(name) - 1
                    
                    if usage <= 0:
                        self.issues.append({
                            'type': 'unused_import',
                            'severity': 'info',
                            'message': f"未使用的导入: {imp}",
                            'file': str(file_path)
                        })
                        
            except Exception as e:
                pass  # 解析错误忽略
    
    def _find_complex_functions(self, py_files: List[Path]):
        """查找复杂函数"""
        for file_path in py_files:
            try:
                content = file_path.read_text(encoding='utf-8')
                tree = ast.parse(content)
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        # 计算圈复杂度 (简化版)
                        lines = node.end_lineno - node.lineno if node.end_lineno else 10
                        
                        if lines > 50:  # 超过50行
                            self.issues.append({
                                'type': 'complex_function',
                                'severity': 'warning',
                                'message': f"函数过长: {node.name} ({lines} 行)",
                                'file': str(file_path),
                                'line': node.lineno
                            })
                        
                        # 检查参数数量
                        arg_count = len(node.args.args)
                        if arg_count > 7:
                            self.issues.append({
                                'type': 'too_many_args',
                                'severity': 'info',
                                'message': f"参数过多: {node.name} ({arg_count} 个)",
                                'file': str(file_path)
                            })
                            
            except Exception as e:
                pass
    
    def _find_code_smells(self, py_files: List[Path]):
        """查找代码异味"""
        smell_patterns = {
            'print_statement': (r'^\s*print\s*\(', "使用print而非logger"),
            'bare_except': (r'except\s*:', "使用裸except (应使用except Exception)"),
            'todo_comment': (r'#\s*TODO', "待办事项"),
            'hardcoded_value': (r'=\s*[0-9]{4,}', "硬编码数值"),
        }
        
        for file_path in py_files:
            try:
                content = file_path.read_text(encoding='utf-8')
                lines = content.split('\n')
                
                for line_num, line in enumerate(lines, 1):
                    for smell_name, (pattern, message) in smell_patterns.items():
                        if re.search(pattern, line, re.IGNORECASE):
                            self.issues.append({
                                'type': f'code_smell_{smell_name}',
                                'severity': 'info',
                                'message': message,
                                'file': str(file_path),
                                'line': line_num
                            })
                            
            except Exception as e:
                pass
    
    def _categorize_issues(self) -> Dict[str, int]:
        """按类型分类问题"""
        categories = defaultdict(int)
        for issue in self.issues:
            categories[issue['type']] += 1
        return dict(categories)
    
    def generate_report(self) -> str:
        """生成分析报告"""
        report = []
        report.append("="*60)
        report.append("代码分析报告")
        report.append("="*60)
        report.append(f"\n总问题数: {len(self.issues)}")
        
        # 按类型统计
        categories = self._categorize_issues()
        if categories:
            report.append("\n问题分类:")
            for issue_type, count in sorted(categories.items(), key=lambda x: -x[1]):
                report.append(f"  {issue_type}: {count}")
        
        # 显示详细问题
        if self.issues:
            report.append("\n详细问题 (前20个):")
            for issue in self.issues[:20]:
                severity = issue['severity'].upper()
                msg = issue['message']
                file_info = issue.get('file', '')
                if 'line' in issue:
                    file_info += f":{issue['line']}"
                
                report.append(f"\n[{severity}] {msg}")
                if file_info:
                    report.append(f"  位置: {file_info}")
        
        report.append("\n" + "="*60)
        return '\n'.join(report)


class RefactorHelper:
    """
    重构辅助工具
    """
    
    @staticmethod
    def extract_common_code(files: List[str], output_file: str):
        """提取公共代码到工具模块"""
        logger.info(f"提取公共代码到 {output_file}")
        # 实际重构需要人工确认，这里仅提供建议
        pass
    
    @staticmethod
    def generate_uml_diagram(src_dir: str, output_file: str):
        """生成类图 (简化文本版)"""
        py_files = Path(src_dir).rglob("*.py")
        
        classes = []
        for file_path in py_files:
            try:
                content = file_path.read_text()
                file_classes = re.findall(r'class\s+(\w+)(?:\((.*?)\))?:', content)
                for cls_name, inheritance in file_classes:
                    classes.append({
                        'name': cls_name,
                        'file': str(file_path),
                        'inherits': inheritance.strip() if inheritance else 'object'
                    })
            except:
                pass
        
        # 生成文本类图
        with open(output_file, 'w') as f:
            f.write("# 类图\n\n")
            for cls in classes:
                f.write(f"## {cls['name']}\n")
                f.write(f"- 文件: {cls['file']}\n")
                f.write(f"- 继承: {cls['inherits']}\n\n")
        
        logger.info(f"类图已生成: {output_file}")


# 便捷函数
def analyze_code_quality(src_dir: str = "src") -> Dict:
    """便捷分析代码质量"""
    analyzer = CodeAnalyzer(src_dir)
    report = analyzer.analyze_project()
    print(analyzer.generate_report())
    return report


# 使用示例
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("="*60)
    print("代码重构辅助工具")
    print("="*60)
    
    # 分析代码 (使用当前目录的示例)
    analyzer = CodeAnalyzer(".")
    
    # 添加一些示例问题
    analyzer.issues = [
        {
            'type': 'unused_import',
            'severity': 'info',
            'message': '未使用的导入: numpy',
            'file': 'src/test.py',
            'line': 1
        },
        {
            'type': 'complex_function',
            'severity': 'warning',
            'message': '函数过长: run_backtest (85 行)',
            'file': 'src/backtest.py',
            'line': 45
        }
    ]
    
    print(analyzer.generate_report())
    
    print("\n" + "="*60)
