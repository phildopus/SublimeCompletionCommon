"""
Copyright (c) 2012 Fredrik Ehnbom

This software is provided 'as-is', without any express or implied
warranty. In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

   1. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software
   in a product, an acknowledgment in the product documentation would be
   appreciated but is not required.

   2. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.

   3. This notice may not be removed or altered from any source
   distribution.
"""
import sublime
import sublime_plugin
import re
import subprocess
import time
import Queue
import threading
from parsehelp import parsehelp

language_regex = re.compile("(?<=source\.)[\w+#]+")
member_regex = re.compile("(([a-zA-Z_]+[0-9_]*)|([\)\]])+)(\.)$")


class CompletionCommonDotComplete(sublime_plugin.TextCommand):
    def run(self, edit):
        for region in self.view.sel():
            self.view.insert(edit, region.end(), ".")
        caret = self.view.sel()[0].begin()
        line = self.view.substr(sublime.Region(self.view.word(caret-1).a, caret))
        if member_regex.search(line) != None:
            sublime.set_timeout(self.delayed_complete, 1)

    def delayed_complete(self):
        self.view.run_command("auto_complete")


class CompletionCommon(object):

    def __init__(self, settingsfile, workingdir):
        self.settingsfile = settingsfile
        self.completion_proc = None
        self.completion_cmd = None
        self.data_queue = Queue.Queue()
        self.workingdir = workingdir

    def get_settings(self):
        return sublime.load_settings(self.settingsfile)

    def get_setting(self, key, default=None):
        try:
            s = sublime.active_window().active_view().settings()
            if s.has(key):
                return s.get(key)
        except:
            pass
        return self.get_settings().get(key, default)

    def get_cmd(self):
        return None

    def completion_thread(self):
        try:
            while True:
                if self.completion_proc.poll() != None:
                    break
                read = self.completion_proc.stdout.readline().strip()
                #print "read: %s" % read
                if read:
                    self.data_queue.put(read)
        finally:
            #print "completion_proc: %d" % (completion_proc.poll())
            self.data_queue.put(";;--;;")
            self.data_queue.put(";;--;;exit;;--;;")
            self.completion_cmd = None
            print "no longer running"

    def run_completion(self, cmd, stdin=None):
        realcmd = self.get_cmd()
        if not self.completion_proc or realcmd != self.completion_cmd:
            if self.completion_proc:
                self.completion_proc.stdin.write("-quit\n")
                while self.data_queue.get() != ";;--;;exit;;--;;":
                    continue

            self.completion_cmd = realcmd
            self.completion_proc = subprocess.Popen(
                realcmd,
                cwd=self.workingdir,
                shell=True,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE
                )
            t = threading.Thread(target=self.completion_thread)
            t.start()
        #print "wrote: %s" % cmd
        self.completion_proc.stdin.write(cmd+"\n")
        if stdin:
            #print "wrote: %s" % stdin
            self.completion_proc.stdin.write(stdin + "\n")
        stdout = ""
        while True:
            try:
                read = self.data_queue.get(timeout=5.0)
                if read == ";;--;;" or read == None:
                    break
                stdout += read+"\n"
            except:
                break
        return stdout

    def get_language(self, view):
        caret = view.sel()[0].a
        scope = view.scope_name(caret).strip()
        language = language_regex.search(scope)
        if language == None:
            if scope.endswith("jsp"):
                return "jsp"
            return None
        return language.group(0)

    def is_supported_language(self, view):
        return False

    def find_absolute_of_type(self, data, full_data, type):
        thispackage = re.search("[ \t]*package (.*);", data)
        if thispackage is None:
            thispackage = ""
        else:
            thispackage = thispackage.group(1)

        match = re.search("class %s" % type, full_data)
        if not match is None:
            # This type is defined in this file so figure out the nesting
            full_data = parsehelp.remove_empty_classes(parsehelp.remove_preprocessing(parsehelp.collapse_brackets(full_data[:match.start()])))
            regex = re.compile("\s*class\s+([^\\s{]+)")
            add = ""
            for m in re.finditer(regex, full_data):
                if len(add):
                    add = "%s$%s" % (add, m.group(1))
                else:
                    add = m.group(1)

            if len(add):
                type = "%s$%s" % (add, type)
            # Class is defined in this file, return package of the file
            if len(thispackage) == 0:
                return type
            return "%s.%s" % (thispackage, type)

        packages = re.findall("[ \t]*import[ \t]+(.*);", data)
        packages.append("java.lang.*")
        packages.append(thispackage + ".*")
        packages.append("")  # for int, boolean, etc
        for package in packages:
            if package.endswith(".%s" % type):
                # Explicit imports, we want these to have the highest
                # priority when searching for the absolute type, so
                # insert them at the top of the package list.
                # Both the .* version and not is added so that
                # blah.<searchedForClass> and blah$<searchedForClass>
                # is tested
                add = package[:-(len(type)+1)]
                packages.insert(0, add + ".*")
                packages.insert(1, add)
                break
        packages.append(";;--;;")

        output = self.run_completion("-findclass %s" % (type), "\n".join(packages)).strip()
        if len(output) == 0 and "." in type:
            return self.find_absolute_of_type(data, full_data, type.replace(".", "$"))
        return output

    def complete_class(self, absolute_classname, prefix):
        stdout = self.run_completion("-complete %s %s" % (absolute_classname, prefix))
        stdout = stdout.split("\n")[:-2]
        members = [tuple(line.split(";;--;;")) for line in stdout]
        ret = []
        for member in members:
            if member not in ret:
                ret.append(member)
        return sorted(ret, key=lambda a: a[0])

    def get_return_type(self, absolute_classname, prefix):
        stdout = self.run_completion("-returntype %s %s" % (absolute_classname, prefix))
        ret = stdout.strip()
        match = re.search("(\[L)?([^;]+)", ret)
        if match:
            return match.group(2)
        return ret

    def on_query_completions(self, view, prefix, locations):
        bs = time.time()
        start = time.time()
        if not self.is_supported_language(view):
            return []
        line = view.substr(sublime.Region(view.full_line(locations[0]).begin(), locations[0]))
        before = line
        if len(prefix) > 0:
            before = line[:-len(prefix)]
        if re.search("[ \t]+$", before):
            before = ""
        elif re.search("\.$", before):
            # Member completion
            data = view.substr(sublime.Region(0, locations[0]))
            full_data = view.substr(sublime.Region(0, view.size()))
            typedef = parsehelp.get_type_definition(data, before)
            if typedef == None:
                return []
            line, column, typename, var, tocomplete = typedef

            if typename is None:
                # This is for completing for example "System."
                # or "String." or other static calls/variables
                typename = var
            start = time.time()
            typename = re.sub("(<.*>)|(\[.*\])", "", typename)
            typename = self.find_absolute_of_type(data, full_data, typename)
            end = time.time()
            print "absolute is %s (%f ms)" % (typename, (end-start)*1000)
            if typename == "":
                return []

            tocomplete = tocomplete[1:]  # skip initial .
            start = time.time()
            idx = tocomplete.find(".")
            while idx != -1:
                sub = tocomplete[:idx]
                idx2 = sub.find("(")
                if idx2 >= 0:
                    sub = sub[:idx2]
                    count = 1
                    for i in range(idx+1, len(tocomplete)):
                        if tocomplete[i] == '(':
                            count += 1
                        elif tocomplete[i] == ')':
                            count -= 1
                            if count == 0:
                                idx = tocomplete.find(".", i)
                                break

                n = self.get_return_type(typename, sub)
                print "%s.%s = %s" % (typename, sub, n)
                typename = n
                tocomplete = tocomplete[idx+1:]
                idx = tocomplete.find(".")
            end = time.time()
            print "finding what to complete took %f ms" % ((end-start) * 1000)

            print "completing %s.%s" % (typename, prefix)
            start = time.time()
            ret = self.complete_class(typename, prefix)
            end = time.time()
            print "completion took %f ms" % ((end-start)*1000)
            be = time.time()
            print "total %f ms" % ((be-bs)*1000)
            return ret

        print "here"
        return []

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "completion_common.is_code":
            caret = view.sel()[0].a
            scope = view.scope_name(caret).strip()
            return re.search("(string.)|(comment.)", scope) == None
